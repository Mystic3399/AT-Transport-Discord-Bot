import hashlib
import hmac
import json
import unittest
from datetime import timezone
from trucky_signed_webhook_v2 import (
    verify_trucky_webhook, extract_trucky_identity_evidence,
    InvalidTruckyWebhook,
)


class SignedWebhookTests(unittest.TestCase):
    def setUp(self):
        self.body = json.dumps({
            "event": "job_completed", "data": {"id": 27805272}
        }).encode("utf-8")
        self.secret = "test-only-signing-secret"
        self.signature = hmac.new(
            self.secret.encode(), self.body, hashlib.sha256
        ).hexdigest()

    def test_valid_signature_still_does_not_award(self):
        result = verify_trucky_webhook(self.body, self.signature, self.secret)
        self.assertEqual(result["status"], "authenticated_pending_schema_verification")
        self.assertFalse(result["verified_delivery"])
        self.assertFalse(result["eligible_for_award"])

    def test_modified_body_is_rejected(self):
        with self.assertRaises(InvalidTruckyWebhook):
            verify_trucky_webhook(self.body + b" ", self.signature, self.secret)

    def test_wrong_secret_is_rejected(self):
        with self.assertRaises(InvalidTruckyWebhook):
            verify_trucky_webhook(self.body, self.signature, "wrong-secret")

    def test_missing_signature_is_rejected(self):
        with self.assertRaises(InvalidTruckyWebhook):
            verify_trucky_webhook(self.body, "", self.secret)

    def test_ignored_event(self):
        body = b'{"event":"job_created","data":{}}'
        sig = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertEqual(
            verify_trucky_webhook(body, sig, self.secret)["status"],
            "ignored_event",
        )

    def test_no_raw_body_canonicalization(self):
        body = b'{ "event": "job_completed", "data": {"id": 27805272} }'
        with self.assertRaises(InvalidTruckyWebhook):
            verify_trucky_webhook(body, self.signature, self.secret)

    def test_extracts_documented_signed_driver_steam_identity(self):
        evidence = extract_trucky_identity_evidence({
            "id": 27805272,
            "user_id": 123456,
            "completed_at": "2026-10-17T20:00:00Z",
            "driver": {
                "id": 123456,
                "steam_profile": {"steam_id": "76561198000000000"},
            },
        })
        self.assertEqual(evidence["provider_job_id"], "27805272")
        self.assertEqual(evidence["trucky_account_id"], "123456")
        self.assertEqual(evidence["steam_id64"], "76561198000000000")
        self.assertEqual(evidence["completed_at"].tzinfo, timezone.utc)

    def test_rejects_driver_id_mismatch(self):
        with self.assertRaisesRegex(InvalidTruckyWebhook, "ambiguous"):
            extract_trucky_identity_evidence({
                "id": 27805272, "user_id": 123457,
                "completed_at": "2026-10-17T20:00:00Z",
                "driver": {"id": 123456,
                           "steam_profile": {"steam_id": "76561198000000000"}},
            })

    def test_rejects_missing_or_invalid_steam_identity(self):
        for steam_id in (None, "7656119800000000", "not-a-steam-id"):
            with self.subTest(steam_id=steam_id):
                with self.assertRaises(InvalidTruckyWebhook):
                    extract_trucky_identity_evidence({
                        "id": 27805272, "completed_at": "2026-10-17T20:00:00Z",
                        "driver": {"id": 123456,
                                   "steam_profile": {"steam_id": steam_id}},
                    })

    def test_rejects_incomplete_or_timezone_naive_job(self):
        base = {"id": 27805272, "driver": {"id": 123456,
                 "steam_profile": {"steam_id": "76561198000000000"}}}
        for timestamp in (None, "not-a-date", "2026-10-17T20:00:00"):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(InvalidTruckyWebhook):
                    extract_trucky_identity_evidence({**base, "completed_at": timestamp})


if __name__ == "__main__":
    unittest.main()
