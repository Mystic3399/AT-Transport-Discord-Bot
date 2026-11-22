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
        self.assertIn("mileage_type = 'race'",body)

    def test_automatic_replay_requires_snapshots_and_exact_accounts(self):
        tree=ast.parse((ROOT/"bot.py").read_text(encoding="utf-8"))
        scan=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                  and n.name=="replay_saved_trucksbook_jobs")
        code=ast.unparse(scan)
        for required in ("trusted_account_id","valid_replay_window",
                         "audit_account_matches","has_valid_tracking_link",
                         "read_only_database_fingerprint","record_real_job"):
            self.assertIn(required,code)
