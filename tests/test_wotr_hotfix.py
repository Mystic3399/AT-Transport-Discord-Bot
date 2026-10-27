"""Run without Discord credentials or connecting to the production database."""
import ast
import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1].joinpath('bot.py').read_text()
TREE = ast.parse(SOURCE)

def load_function(name, namespace):
    node = next(n for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), namespace)
    return namespace[name]

from datetime import datetime, timezone
NS = {'re': re, 'datetime': datetime, 'timezone': timezone}
for node in TREE.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {'TRUCKSBOOK_NAME_ALIASES', 'MILEAGE_AUDIT_CHANNEL_IDS', 'MILEAGE_LINK_RECOVERY_SINCE', 'VERIFIED_HISTORICAL_REAL_REPAIRS'} for t in node.targets):
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), NS)
for name in ('is_real_statistics', 'extract_integer', 'extract_trucksbook_job_from_embed', 'trucksbook_author_user_id', 'verified_historical_real_repair'):
    load_function(name, NS)

class Embed:
    def __init__(self, job_id='68007306', statistics='Real, WoTr', distance=103):
        self.data = {'title': f'Job delivery #{job_id}', 'author': {'name': 'TruckTan'},
                     'fields': [{'name': 'Details', 'value': f'Cargo: Halloween Crafts\nAccepted distance: {distance} mil\nStatistics: {statistics}'}]}
    def to_dict(self):
        return self.data

class ClassificationTests(unittest.TestCase):
    def test_real_and_event(self):
        for value in ('Real', 'Real, WoTr', ' real , wotr ', 'WoTr, Real'):
            with self.subTest(value=value):
                self.assertTrue(NS['is_real_statistics'](value))
    def test_invalid_not_promoted(self):
        for value in (None, '', 'WoTr', 'Invalid', 'Invalid, WoTr', 'Race, WoTr', 'Not Real', 'Real, Invalid', 'Real, Race, WoTr', 'Real,', 'Real, Real'):
            with self.subTest(value=value):
                self.assertFalse(NS['is_real_statistics'](value))
    def test_screenshot_deliveries(self):
        for job_id, miles in [('68007306', 103), ('68007834', 153)]:
            parsed = NS['extract_trucksbook_job_from_embed'](Embed(job_id, distance=miles))
            self.assertEqual(parsed, {'job_id': job_id, 'statistics': 'Real, WoTr', 'accepted_distance': miles})
    def test_live_forwarding_and_duplicate_path(self):
        async def run():
            calls = []
            async def noop(*args): pass
            async def find(name, provider_id=None): return 42
            async def record(**kwargs):
                calls.append(kwargs)
                return {'status': 'duplicate'}
            ns = dict(NS, bot=SimpleNamespace(get_guild=lambda _: None, process_commands=noop),
                      GUILD_ID=1, find_driver_link=find, record_real_job=record)
            handler = load_function('on_message', ns)
            for statistics in ('Real, WoTr', 'Real', 'Invalid, WoTr', 'Race, WoTr', 'WoTr'):
                await handler(SimpleNamespace(webhook_id=9, channel=SimpleNamespace(id=22), embeds=[Embed(statistics=statistics)]))
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]['accepted_distance'], 103)
            self.assertEqual(calls[0]['discord_user_id'], 42)
        asyncio.run(run())

class RecoveryTests(unittest.TestCase):
    def test_preview_apply_retry_and_scope(self):
        async def run():
            recorded = set()
            credited = []
            replies = []
            class Connection:
                async def fetchval(self, sql, job_id): return 1 if job_id in recorded else None
            class Acquire:
                async def __aenter__(self): return Connection()
                async def __aexit__(self, *args): pass
            class Channel:
                def history(self, **kwargs):
                    self.kwargs = kwargs
                    async def iterate():
                        for message in messages: yield message
                    return iterate()
            messages = [SimpleNamespace(webhook_id=9, embeds=[Embed()]),
                        SimpleNamespace(webhook_id=9, embeds=[Embed()]),
                        SimpleNamespace(webhook_id=8, embeds=[Embed('999')]),
                        SimpleNamespace(webhook_id=9, embeds=[Embed('888', 'Invalid, WoTr')]),
                        SimpleNamespace(webhook_id=9, embeds=[Embed('777', 'Real')])]
            async def reply(*args, **kwargs): replies.append(args[0])
            async def find(name, provider_id=None): return 42
            async def replay(message):
                parsed = NS['extract_trucksbook_job_from_embed'](message.embeds[0])
                if parsed['job_id'] not in recorded:
                    recorded.add(parsed['job_id'])
                    credited.append(parsed['accepted_distance'])
            channel = Channel()
            ctx = SimpleNamespace(guild=SimpleNamespace(id=1), channel=channel,
                                  message=object(), reply=reply, send=reply)
            ns = dict(NS, GUILD_ID=1, db_pool=SimpleNamespace(acquire=Acquire),
                      discord=SimpleNamespace(Forbidden=type('Forbidden', (Exception,), {}),
                                              HTTPException=type('HTTPException', (Exception,), {}),
                                              AllowedMentions=SimpleNamespace(none=lambda: None)),
                      find_driver_link=find, on_message=replay)
            recover = load_function('wot_recover', ns)
            await recover(ctx, 9)
            self.assertEqual(credited, [])
            self.assertIn('to recover: 103', replies[-1])
            await recover(ctx, 9, 1000, 'CONFIRM')
            await recover(ctx, 9, 1000, 'CONFIRM')
            self.assertEqual(credited, [103])
            self.assertIs(channel.kwargs['before'], ctx.message)
            self.assertFalse(channel.kwargs['oldest_first'])
        asyncio.run(run())


class AuditApplyTests(unittest.TestCase):
    def test_audit_apply_preserves_totals_and_retry(self):
        async def run():
            from datetime import datetime, timezone
            delivered = datetime(2026, 10, 15, 20, tzinfo=timezone.utc)
            candidates = {}
            jobs = {'123': {'discord_user_id': 42, 'trucksbook_name': 'TruckTan',
                            'accepted_distance': 3, 'statistics': 'Real'}}
            total = [13]
            replies = []
            syncs = []
            class Lease:
                async def __aenter__(self): return connection
                async def __aexit__(self, *args): pass
            class Transaction:
                async def __aenter__(self): pass
                async def __aexit__(self, *args): pass
            class Connection:
                def transaction(self): return Transaction()
                async def fetchrow(self, sql, *args):
                    if 'FROM processed_jobs' in sql: return jobs.get(args[0])
                    if 'COUNT(*) AS jobs' in sql:
                        pending = [c for c in candidates.values() if not c.get('applied')]
                        return {'jobs': len(pending), 'drivers': len({c['discord_user_id'] for c in pending}), 'missing_miles': sum(c['missing_distance'] for c in pending)}
                    if 'FROM driver_progress' in sql: return {'real_miles': total[0], 'trucksbook_name': 'TruckTan'}
                    raise AssertionError(sql)
                async def fetch(self, sql, *args):
                    if 'FROM driver_links' in sql: return []
                    return [c for c in candidates.values() if not c.get('applied')]
                async def fetchval(self, sql, *args):
                    if 'SELECT real_miles' in sql: return total[0]
                    if 'INSERT INTO processed_jobs' in sql:
                        self.asserted_insert = 'ON CONFLICT (job_id) DO NOTHING' in sql
                        if args[0] in jobs: return None
                        jobs[args[0]] = {'discord_user_id': args[1], 'trucksbook_name': args[2], 'accepted_distance': args[3],
                                         'statistics': 'Real', 'processed_at': args[4]}
                        return args[0]
                    raise AssertionError(sql)
                async def execute(self, sql, *args):
                    if 'INSERT INTO mileage_repair_candidates' in sql:
                        if not candidates.get(args[0], {}).get('applied'):
                            candidates[args[0]] = dict(zip(['job_id','discord_user_id','trucksbook_name','recorded_distance','actual_distance','missing_distance','source_channel_id','source_message_id','repair_kind','source_delivered_at'], args))
                    elif 'UPDATE mileage_repair_candidates' in sql: candidates[args[0]]['applied'] = True
                    elif 'INSERT INTO driver_progress' in sql: total[0] += args[2]
                    elif 'UPDATE processed_jobs' in sql: jobs[args[0]]['accepted_distance'] = args[1]
                    else: raise AssertionError(sql)
            connection = Connection()
            async def reply(*args, **kwargs): replies.append(args[0])
            async def find(name, provider_id=None): return 42 if name == 'TruckTan' and provider_id == 100 else None
            async def sync(*args, **kwargs): syncs.append(args)
            async def announce(*args): return (0, 0)
            reports = []
            async def reconcile(ctx, sources, metadata): reports.append(sources)
            event = Embed()
            event.data['author']['url'] = 'https://trucksbook.eu/user/100'
            unlinked = Embed('125'); unlinked.data['author']['name'] = 'Unknown'
            modern = Embed('127', 'Real', 120)
            modern.data['author']['url'] = 'https://trucksbook.eu/profile/100'
            class Channel:
                id = 22
                def permissions_for(self, _): return SimpleNamespace(view_channel=True, read_message_history=True)
                def history(self, **kwargs):
                    async def gen():
                        for i, e in enumerate([event, event, Embed('123', 'Real', 103), Embed('124', 'Real', 500), unlinked, Embed('126', 'Race, WoTr'), modern]):
                            yield SimpleNamespace(webhook_id=9, embeds=[e], id=i, created_at=(datetime(2026, 10, 18, tzinfo=timezone.utc) if e is modern else delivered))
                    return gen()
            channel = Channel()
            guild = SimpleNamespace(id=1, me=object(), text_channels=[channel, SimpleNamespace(id=999, name='unrelated', permissions_for=lambda _: SimpleNamespace(view_channel=False, read_message_history=False))])
            ctx = SimpleNamespace(guild=guild,channel=channel,reply=reply,send=reply,author=SimpleNamespace(id=99))
            discord = SimpleNamespace(Forbidden=type('Forbidden', (Exception,), {}), HTTPException=type('HTTPException', (Exception,), {}))
            ns = dict(NS, MILEAGE_AUDIT_CHANNEL_IDS={22: 'fixture'}, json=__import__('json'), db_pool=SimpleNamespace(acquire=Lease), GUILD_ID=1, discord=discord,
                      find_driver_link=find, PROGRESSION_ROLES=[], sync_member_progression_role=sync,
                      sync_permanent_system_for_driver=sync, announce_pending_repair_achievements=announce,
                      refresh_driver_leaderboard=sync, send_mileage_reconciliation=reconcile)
            from mileage_audit_confirmation_v2 import AuditPreviewError,explicit_audit_miles
            async def preview(*args):
                selected=[c for c in candidates.values() if not c.get('applied') and c['job_id'] in args[-1]]
                return dict(preview_id=1,jobs=len(selected),addition=sum(c['missing_distance'] for c in selected),drivers=[],held_jobs=0)
            async def validate(*args):return dict(preview_id=1,job_ids=set(c['job_id'] for c in args[-1]))
            async def consume(*args):pass
            ns.update(AuditPreviewError=AuditPreviewError,audit_source_allowed=lambda id:id==9,
                explicit_audit_miles=explicit_audit_miles,create_mileage_audit_preview=preview,
                validate_mileage_audit_preview=validate,consume_mileage_audit_preview=consume)
            from unittest.mock import AsyncMock
            ns['read_pending_race']=AsyncMock(return_value=dict(verified_pending_miles=0,verified_pending_jobs=0,
                unverified_miles=0,unverified_jobs=0,classification_pending_jobs=0,conflicting_jobs=0))
            audit = load_function('mileage_audit', ns)
            apply = load_function('mileage_apply', ns)
            await audit(ctx)
            self.assertEqual(total[0], 13)  # preview never credits mileage
            self.assertEqual(set(candidates), {'68007306', '123', '127'})
            self.assertEqual(candidates['123']['missing_distance'], 100)
            self.assertEqual(candidates['68007306']['repair_kind'], 'missing_wotr')
            self.assertEqual(candidates['127']['repair_kind'], 'missing_real')
            self.assertTrue(any('Mileage channels scanned: `1`' in text for text in replies))
            self.assertTrue(any('Missing Race Miles (Trucky, stored pending)' in text for text in replies))
            self.assertTrue(any('Historical Real jobs held for baseline review: `1` / `500` miles' in text for text in replies))
            self.assertNotIn('124', candidates)
            self.assertEqual(len(reports[0]), 5)  # unique Real jobs, including older excluded jobs
            self.assertIn('124', {job['job_id'] for job in reports[0]})
            self.assertTrue(any('driver not linked: `1`' in text for text in replies))
            await apply(ctx)  # explicit confirmation is required
            self.assertEqual(total[0], 13)
            await apply(ctx, 'CONFIRM')
            self.assertEqual(total[0], 336)  # existing 13 + event 103 + correction 100 + incident Real 120
            self.assertEqual(jobs['68007306']['processed_at'], delivered)
            self.assertTrue(connection.asserted_insert)
            self.assertGreater(len(syncs), 0)
            await audit(ctx)
            await apply(ctx, 'CONFIRM')
            self.assertEqual(total[0], 336)
            # If a live delivery wins after audit, consume its candidate with no credit.
            candidates['68007306']['applied'] = False
            await apply(ctx, 'CONFIRM')
            self.assertEqual(total[0], 336)
        asyncio.run(run())

if __name__ == '__main__': unittest.main()

