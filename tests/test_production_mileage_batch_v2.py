"""The connected live worker remains closed until genuine acceptance is supplied."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from unified_production_ledger_adapter_v2 import read_approved_mileage_preflight,process_approved_mileage_batch


class ProductionMileageBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_and_staging_cannot_touch_a_database(self):
        for env in ({},{'AT_V2_ISOLATED_STAGING':'YES','AT_V2_PRODUCTION_MILEAGE_ENABLED':'YES'},
                    {'AT_V2_TEST_DATABASE_DISPOSABLE':'YES','AT_V2_PRODUCTION_MILEAGE_ENABLED':'YES'},
                    {'AT_V2_MILEAGE_ENGINE_TEST_ENABLED':'YES','AT_V2_PRODUCTION_MILEAGE_ENABLED':'YES'}):
            with patch.dict(os.environ,env,clear=True):
                pool=MagicMock()
                self.assertIsNone(read_approved_mileage_preflight())
                self.assertEqual((await process_approved_mileage_batch(pool))['status'],'disabled')
                pool.acquire.assert_not_called()

    async def test_approval_and_valid_batch_are_required_before_database_access(self):
        with patch.dict(os.environ,{'AT_V2_PRODUCTION_MILEAGE_ENABLED':'YES'},clear=True):
            for runtime in (None,{}, {'mileage_jobs':[]}, {'mileage_jobs':[{'provider':'trucksbook','provider_job_id':'123'}]},
                            {'mileage_jobs':[{'provider':'trucky','provider_job_id':'1'}]*2}):
                pool=MagicMock()
                result=await process_approved_mileage_batch(pool,runtime=runtime)
                self.assertIn(result['status'],('activation_blocked','invalid_batch'))
                pool.acquire.assert_not_called()

    async def test_mileage_manifest_is_checksum_bound_and_independent_of_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'acceptance.json';raw=json.dumps({'mileage_jobs':[]}).encode();path.write_bytes(raw)
            env={'AT_V2_PRODUCTION_MILEAGE_ENABLED':'YES','AT_V2_PROGRESSION_ENABLED':'NO',
                 'AT_V2_APPROVED_PREFLIGHT_PATH':str(path),'AT_V2_APPROVED_PREFLIGHT_SHA256':hashlib.sha256(raw).hexdigest()}
            result=read_approved_mileage_preflight(env)
            self.assertEqual(result['mileage_jobs'],[])
            self.assertEqual(env['AT_V2_PROGRESSION_ENABLED'],'NO')
            path.write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError,'checksum'):read_approved_mileage_preflight(env)

    async def test_running_poll_reaches_approved_batch_after_retaining_history(self):
        import ast
        from unittest.mock import AsyncMock
        import trucky_jobs_api_v2 as api
        import unified_production_ledger_adapter_v2 as ledger
        fn=next(n for n in ast.parse(Path('bot.py').read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='ingest_trucky_company_jobs')
        fn.decorator_list=[]
        pool=object();ns={'os':os,'db_pool':pool}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
        approved={'mileage_jobs':[]}
        with patch.dict(os.environ,{'AT_V2_TRUCKY_API_INGEST_ENABLED':'YES','AT_V2_PENDING_HISTORY_ENABLED':'YES'},clear=True),patch.object(api,'ingest_authenticated_completed_jobs',AsyncMock(return_value={'status':'evidence_processed'})) as ingest,patch.object(api,'sweep_saved_trucky_history',AsyncMock(return_value={'status':'history_page_processed'})) as sweep,patch.object(ledger,'read_approved_mileage_preflight',return_value=approved),patch.object(ledger,'process_approved_mileage_batch',AsyncMock(return_value={'status':'processed','mileage_awarded':False})) as batch:
            await ns['ingest_trucky_company_jobs']()
            ingest.assert_awaited_once_with(pool)
            sweep.assert_awaited_once_with(pool)
            batch.assert_awaited_once_with(pool,runtime=approved)

    async def test_invalid_activation_manifest_does_not_stop_history_capture(self):
        import ast
        from unittest.mock import AsyncMock
        import trucky_jobs_api_v2 as api
        import unified_production_ledger_adapter_v2 as ledger
        fn=next(n for n in ast.parse(Path('bot.py').read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='ingest_trucky_company_jobs')
        fn.decorator_list=[];ns={'os':os,'db_pool':object()}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
        with patch.dict(os.environ,{'AT_V2_TRUCKY_API_INGEST_ENABLED':'YES','AT_V2_PENDING_HISTORY_ENABLED':'YES'},clear=True),patch.object(api,'ingest_authenticated_completed_jobs',AsyncMock(return_value={})) as ingest,patch.object(api,'sweep_saved_trucky_history',AsyncMock(return_value={})) as sweep,patch.object(ledger,'read_approved_mileage_preflight',side_effect=ValueError('No acceptance manifest')),patch.object(ledger,'process_approved_mileage_batch',AsyncMock()) as batch:
            await ns['ingest_trucky_company_jobs']()
            ingest.assert_awaited_once();sweep.assert_awaited_once();batch.assert_not_awaited()

    async def test_old_event_comparison_uses_only_exact_authenticated_history(self):
        from datetime import datetime,timezone
        from decimal import Decimal
        from unified_distance_units import KM_TO_MILES
        from unified_production_ledger_adapter_v2 import comparison_metadata_from_history
        event=dict(provider_job_id='123',provider_account_id='456',discord_user_id=789,
                   delivered_at=datetime(2026,10,19,12,tzinfo=timezone.utc))
        payload=dict(mileage_type='real',canonical_miles=str((Decimal(100)*KM_TO_MILES).quantize(Decimal('.0000000001'))),job_metadata=None)
        evidence=dict(game_id=1,status='completed',stats_type='real_miles',driven_distance_km=100,
            distance_unit='km',completed_at='2026-10-19T12:00:00Z',game='ETS2',origin='Paris',destination='Lille',cargo='Pet Food')
        row=dict(provider='trucky',provider_job_id='123',provider_account_id='456',source_kind='authenticated_api',evidence=evidence)
        self.assertEqual(comparison_metadata_from_history(event,payload,[row]),
                         dict(game='ETS2',origin='Paris',destination='Lille',cargo='Pet Food'))
        for bad in ({**row,'source_kind':'owner_export'}, {**row,'provider_account_id':'999'},
                    {**row,'provider_job_id':'999'}, {**row,'evidence':{**evidence,'driven_distance_km':101}},
                    {**row,'evidence':{**evidence,'completed_at':'2026-10-19T12:01:00Z'}},
                    {**row,'evidence':{**evidence,'stats_type':'race_miles'}}):
            self.assertIsNone(comparison_metadata_from_history(event,payload,[bad]))
        conflict={**row,'evidence':{**evidence,'origin':'London'}}
        self.assertIsNone(comparison_metadata_from_history(event,payload,[row,conflict]))
        self.assertIsNone(payload['job_metadata'])
