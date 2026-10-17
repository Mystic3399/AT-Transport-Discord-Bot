import hashlib
import hmac
import json
import unittest
from trucky_signed_webhook_v2 import verify_trucky_webhook, InvalidTruckyWebhook


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


if __name__ == "__main__":
    unittest.main()
