"""Automation cannot guess identity, historical balances or missing job evidence."""
import ast
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

from automatic_mileage_source_recovery_v2 import (
    valid_replay_window, trusted_account_id, temporally_independent,
    valid_temporal_trucksbook_observation
)

NOW=datetime.now(timezone.utc)
ROOT=Path(__file__).resolve().parents[1]

class AutomaticSourceProofTests(unittest.TestCase):
    def evidence(self, posted=NOW, **kw):
        return dict(unit="mi",stats_type="Real",distance=120,
                    source_message_at=posted.isoformat(),**kw)

    def test_only_new_jobs_after_known_snapshot_can_replay(self):
        ev=self.evidence()
        self.assertTrue(valid_replay_window(ev,NOW-timedelta(days=3)))
        self.assertFalse(valid_replay_window(ev,None))
        self.assertFalse(valid_replay_window(ev,NOW-timedelta(minutes=40)))
        self.assertFalse(valid_replay_window(ev,NOW+timedelta(hours=1)))
        self.assertFalse(valid_replay_window(dict(ev,unit="km"),NOW-timedelta(days=3)))
        self.assertFalse(valid_replay_window(dict(ev,distance=120.5),NOW-timedelta(days=3)))

    def test_no_name_or_invalid_account_is_an_ownership_proof(self):
        for account in ("", "A&T Driver", "0", "123 456", "001",None):
            self.assertFalse(trusted_account_id(account))
        self.assertTrue(trusted_account_id("553238"))

    def test_trusted_time_only_can_exclude_distant_delivery_not_close_overlap(self):
        ev=self.evidence(NOW-timedelta(hours=5))
        posted=valid_temporal_trucksbook_observation(ev,120)
        self.assertIsNotNone(posted)
        self.assertTrue(temporally_independent(NOW,posted))
        self.assertFalse(temporally_independent(NOW-timedelta(hours=4),posted))
        self.assertIsNone(valid_temporal_trucksbook_observation(dict(ev,distance=121),120))
        self.assertIsNone(valid_temporal_trucksbook_observation(dict(ev,unit="km"),120))
        self.assertIsNone(valid_temporal_trucksbook_observation(dict(ev,stats_type="Race"),120))

    def test_wotr_real_jobs_are_included_in_duplicate_matching(self):
        text=(ROOT/"automatic_linked_mileage_v2.py").read_text(encoding="utf-8")
        self.assertIn("LOWER(p.statistics) LIKE 'real%'",text)
        self.assertIn("LOWER(statistics) LIKE 'real%'",text)
        self.assertNotIn("LOWER(p.statistics)='real'",text)

    def test_history_scan_records_proof_not_direct_credit(self):
        tree=ast.parse((ROOT/"bot.py").read_text(encoding="utf-8"))
        scan=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                  and n.name=="capture_configured_mileage_history")
        body=ast.unparse(scan)
        self.assertIn("MILEAGE_AUDIT_CHANNEL_IDS",body)
        self.assertIn("audit_source_allowed",body)
        self.assertIn("retain_trucksbook_source_observation",body)
        self.assertNotIn("record_real_job(",body)

    def test_legacy_writer_blocks_double_credit_with_trucky_race(self):
        tree=ast.parse((ROOT/"bot.py").read_text(encoding="utf-8"))
        scan=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                  and n.name=="record_real_job")
        body=ast.unparse(scan)
        self.assertIn("pending_race_cross_provider_review",body)
        self.assertIn("pending_race_cross_provider_evidence",body)
        self.assertIn("valid_temporal_trucksbook_observation",body)
        self.assertIn("mileage_type='race'",body)

    def test_background_leaderboard_updates_do_not_spam_discord(self):
        text=(ROOT/"bot.py").read_text(encoding="utf-8")
        self.assertIn("@tasks.loop(minutes=10)",text)
        self.assertIn("async def leaderboard_rollover_task",text)
        self.assertIn("await asyncio.sleep(600)",text)
        self.assertIn("WHERE LOWER(statistics) LIKE 'real%'",text)

    def test_auto_replay_updates_leaderboard_once_per_batch(self):
        tree=ast.parse((ROOT/"bot.py").read_text(encoding="utf-8"))
        scan=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                  and n.name=="replay_saved_trucksbook_jobs")
        code=ast.unparse(scan)
        self.assertEqual(code.count("await refresh_driver_leaderboard(guild)"),1)
        self.assertIn("affected_users",code)

    def test_automatic_replay_requires_snapshots_and_exact_accounts(self):
        tree=ast.parse((ROOT/"bot.py").read_text(encoding="utf-8"))
        scan=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                  and n.name=="replay_saved_trucksbook_jobs")
        code=ast.unparse(scan)
        for required in ("trusted_account_id","valid_replay_window",
                         "audit_account_matches","has_valid_tracking_link",
                         "read_only_database_fingerprint","record_real_job"):
            self.assertIn(required,code)


class VerifiedTimeOnlyReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_automatically_credits_independent_verified_real_without_route(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock,patch
        from automatic_linked_mileage_v2 import reconcile_trucksbook
        source={"evidence":dict(stats_type="Real",distance=140,unit="mi",
                source_message_at=(NOW-timedelta(hours=6)).isoformat()),
                "provider_account_id":"553238"}
        credits=[{"provider_job_id":"200", "miles":Decimal("190"),
                  "delivered_at":NOW}]
        conn=SimpleNamespace(fetch=AsyncMock(side_effect=[[source],credits]))
        with patch("mileage_audit_confirmation_v2.audit_account_matches",
                   new_callable=AsyncMock,return_value=True) as link:
            self.assertEqual(await reconcile_trucksbook(conn,"100",42,140),
                             (Decimal(140),None))
            link.assert_awaited_once()

    async def test_nearby_real_credit_cannot_be_duplicated_without_route(self):
        from decimal import Decimal
        from unittest.mock import AsyncMock,patch
        from automatic_linked_mileage_v2 import reconcile_trucksbook
        source={"evidence":dict(stats_type="Real",distance=140,unit="mi",
                source_message_at=(NOW-timedelta(minutes=20)).isoformat()),
                "provider_account_id":"553238"}
        credits=[{"provider_job_id":"200", "miles":Decimal("190"),
                  "delivered_at":NOW}]
        conn=SimpleNamespace(fetch=AsyncMock(side_effect=[[source],credits]))
        with patch("mileage_audit_confirmation_v2.audit_account_matches",
                   new_callable=AsyncMock,return_value=True):
            self.assertEqual(await reconcile_trucksbook(conn,"100",42,140),
                             (None,"route_evidence_pending"))

    async def test_unverified_account_cannot_use_temporal_fallback(self):
        from unittest.mock import AsyncMock,patch
        from automatic_linked_mileage_v2 import reconcile_trucksbook
        source={"evidence":dict(stats_type="Real",distance=140,unit="mi",
                source_message_at=(NOW-timedelta(hours=6)).isoformat()),
                "provider_account_id":"553238"}
        conn=SimpleNamespace(fetch=AsyncMock(return_value=[source]))
        with patch("mileage_audit_confirmation_v2.audit_account_matches",
                   new_callable=AsyncMock,return_value=False):
            self.assertEqual(await reconcile_trucksbook(conn,"100",42,140),
                             (None,"unverified_trucksbook_account"))

    def test_legacy_completed_registration_is_considered_for_auto_ownership(self):
        tree=ast.parse((ROOT/"bot.py").read_text(encoding="utf-8"))
        scan=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                  and n.name=="replay_saved_trucksbook_jobs")
        body=ast.unparse(scan)
        self.assertIn("driver_verifications",body)
        self.assertIn("v.status='completed'",body)
        self.assertIn("v.completed_at IS NOT NULL",body)
