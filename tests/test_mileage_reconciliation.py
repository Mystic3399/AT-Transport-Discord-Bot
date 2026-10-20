import asyncio
import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from test_wotr_hotfix import NS, load_function


class ReconciliationTests(unittest.TestCase):
    def test_snapshot_is_readonly_and_missing_jobs_are_not_credited(self):
        async def run():
            progress = [{'discord_user_id': 42, 'trucksbook_name': 'bezzy', 'real_miles': 1602}]
            stored = [{'job_id': 'old', 'discord_user_id': 42, 'trucksbook_name': 'bezzy',
                       'accepted_distance': 1602, 'statistics': 'Real'},
                      {'job_id': 'race', 'discord_user_id': 42, 'trucksbook_name': 'bezzy',
                       'accepted_distance': 500, 'statistics': 'Race'}]
            class Lease:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def transaction(self, **kwargs):
                    self_test.assertEqual(kwargs, {'isolation': 'repeatable_read', 'readonly': True})
                    return self
                async def fetch(self, sql):
                    self_test.assertTrue(sql.startswith('SELECT '))
                    return progress if 'FROM driver_progress' in sql else stored
            self_test = self
            files = []
            async def send(*args, **kwargs): files.append(kwargs['file'])
            async def find(name, provider_id): return 42 if provider_id == 583698 else None
            ns = dict(NS, BytesIO=BytesIO, json=json, db_pool=SimpleNamespace(acquire=Lease),
                      find_driver_link=find, print=lambda *args: None,
                      discord=SimpleNamespace(File=lambda fp, filename: json.loads(fp.getvalue()),
                          AllowedMentions=SimpleNamespace(none=lambda: None)))
            helper = load_function('send_mileage_reconciliation', ns)
            sources = [{'job_id': 'old', 'name': 'bezzy', 'provider_id': 583698,
                        'accepted_distance': 1602, 'statistics': 'Real'},
                       {'job_id': '67800837', 'name': 'bezzy', 'provider_id': 583698,
                        'accepted_distance': 772, 'statistics': 'Real'},
                       {'job_id': 'unknown', 'name': 'unknown', 'provider_id': None,
                        'accepted_distance': 100, 'statistics': 'Real'}]
            await helper(SimpleNamespace(send=send), sources, {'channels_skipped': 0})
            report = files[0]
            driver = report['drivers'][0]
            self.assertEqual(driver['real_miles'], 1602)
            self.assertEqual(driver['source_real_miles'], 2374)
            self.assertEqual(driver['stored_real_job_miles'], 1602)
            self.assertEqual(driver['absent_source_miles'], 772)
            self.assertEqual(driver['total_minus_stored_jobs'], 0)
            self.assertEqual({j['job_id'] for j in report['absent_jobs']}, {'67800837', 'unknown'})
            self.assertIsNone(report['absent_jobs'][1]['linked_discord_user_id'])
            self.assertEqual(progress[0]['real_miles'], 1602)
        asyncio.run(run())

class VerifiedHistoryTests(unittest.TestCase):
    def test_only_exact_verified_source_and_identity_are_eligible(self):
        from test_wotr_hotfix import Embed
        check = NS['verified_historical_real_repair']
        e = Embed('67800837', 'Real', 772)
        e.data['author']['url'] = 'https://trucksbook.eu/profile/583698'
        job = NS['extract_trucksbook_job_from_embed'](e)
        msg = SimpleNamespace(id=1556301076901142614,
                              channel=SimpleNamespace(id=1541473875773362276))
        self.assertTrue(check(job, e, msg, 146691735865655297))
        self.assertFalse(check(job, e, msg, 99))
        self.assertFalse(check(dict(job, accepted_distance=773), e, msg))
        self.assertFalse(check(dict(job, statistics='Race'), e, msg))
        self.assertFalse(check(dict(job, job_id='67866354'), e, msg))
        msg.id = 123
        self.assertFalse(check(job, e, msg))
