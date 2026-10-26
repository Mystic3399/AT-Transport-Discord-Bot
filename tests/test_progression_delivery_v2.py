"""Production effect delivery remains disabled without verified activation."""
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unified_production_ledger_adapter_v2 import deliver_pending_progression, read_approved_progression_preflight

class ProgressionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_and_staging_do_not_touch_database_or_discord(self):
        callback = AsyncMock()
        result = await deliver_pending_progression(None,callback,config={})
        self.assertEqual(result['status'],'disabled'); callback.assert_not_awaited()
        self.assertIsNone(read_approved_progression_preflight({}))

    def test_preflight_must_match_owner_approved_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'preflight.json';raw=json.dumps({'legacy_writer_drained':True}).encode();path.write_bytes(raw)
            config={'AT_V2_PROGRESSION_ENABLED':'YES','AT_V2_APPROVED_PREFLIGHT_PATH':str(path),
                    'AT_V2_APPROVED_PREFLIGHT_SHA256':hashlib.sha256(raw).hexdigest()}
            facts=read_approved_progression_preflight(config)
            self.assertTrue(facts['legacy_writer_drained'])
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'checksum'):
                read_approved_progression_preflight(config)

    async def test_disabled_company_mods_gate_preserves_existing_awards(self):
        source=ast.parse(Path('bot.py').read_text())
        fn=next(n for n in source.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='sync_member_permanent_roles')
        roles={40:object(),100:object()}
        member=SimpleNamespace(roles=[],add_roles=AsyncMock(),remove_roles=AsyncMock())
        guild=SimpleNamespace(get_member=lambda _:member,get_role=lambda role:roles.get(role))
        ns={'ACHIEVEMENT_ROLES':[(40000,40,'Company Mods'),(100000,100,'100k')],
            'HALL_OF_FAME_ROLES':[],'get_hall_of_fame_tier':lambda _:None}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bot.py','exec'),ns)
        await ns['sync_member_permanent_roles'](guild,123,100000,company_mods_eligible=False)
        self.assertEqual(member.add_roles.call_args.args,(roles[100],))
        member.remove_roles.assert_not_awaited()
        member.add_roles.reset_mock()
        await ns['sync_member_permanent_roles'](guild,123,0,company_mods_eligible=True)
        self.assertEqual(member.add_roles.call_args.args,(roles[40],))
