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

NS = {'re': re}
for name in ('is_real_statistics', 'extract_integer', 'extract_trucksbook_job_from_embed'):
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
            async def find(name): return 42
            async def record(**kwargs):
                calls.append(kwargs)
                return {'status': 'duplicate'}
            ns = dict(NS, bot=SimpleNamespace(get_guild=lambda _: None, process_commands=noop),
                      GUILD_ID=1, find_driver_link=find, record_real_job=record)
            handler = load_function('on_message', ns)
            for statistics in ('Real, WoTr', 'Real', 'Invalid, WoTr', 'Race, WoTr', 'WoTr'):
                await handler(SimpleNamespace(webhook_id=9, embeds=[Embed(statistics=statistics)]))
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
            async def find(name): return 42
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

if __name__ == '__main__': unittest.main()
