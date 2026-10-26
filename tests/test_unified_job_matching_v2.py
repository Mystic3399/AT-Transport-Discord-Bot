"""Conservative Trucky/TrucksBook cross-provider matching tests."""
import unittest
from datetime import datetime, timedelta, timezone

from unified_job_matching_v2 import is_candidate_duplicate

BASE = {
    "provider": "trucky",
    "discord_user_id": 123,
    "mileage_type": "real",
    "metadata": {"game": "ets2", "origin": "London", "destination": "Duisburg", "cargo": "Machinery"},
    "delivered_at": datetime(2026, 10, 17, 12, tzinfo=timezone.utc),
    "miles": "200.00",
}


class MatchingTests(unittest.TestCase):
    def test_cross_provider_candidate(self):
        self.assertTrue(is_candidate_duplicate(BASE, {**BASE, "provider": "trucksbook",
            "delivered_at": BASE["delivered_at"] + timedelta(minutes=15),
            "miles": "202.00"}))

    def test_not_same_provider_or_different_driver(self):
        self.assertFalse(is_candidate_duplicate(BASE, dict(BASE)))
        self.assertFalse(is_candidate_duplicate(BASE, {**BASE, "provider": "trucksbook",
            "discord_user_id": 124}))

    def test_mismatch_and_time_window(self):
        for changed in (
            {"mileage_type": "race"},
            {"metadata": {**BASE["metadata"], "destination": "Berlin"}},
            {"delivered_at": BASE["delivered_at"] + timedelta(hours=2)},
            {"miles": "230.00"},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(is_candidate_duplicate(
                    BASE, {**BASE, "provider": "trucksbook", **changed}
                ))

    def test_unknown_mileage_classification_never_matches(self):
        self.assertFalse(is_candidate_duplicate(
            {**BASE, "mileage_type": None},
            {**BASE, "provider": "trucksbook", "mileage_type": None}
        ))

    def test_missing_metadata_fails_closed(self):
        with self.assertRaises(ValueError):
            is_candidate_duplicate(BASE, {**BASE, "provider": "trucksbook",
                "metadata": {"game": "ets2"}})


    def test_observed_ets2_prague_localisation_is_candidate_only(self):
        first={**BASE,'metadata':{**BASE['metadata'],'origin':'Mannheim','destination':'Praha','cargo':'Medical Equipment'}}
        other={**first,'provider':'trucksbook','metadata':{**first['metadata'],'destination':'Prague'}}
        self.assertTrue(is_candidate_duplicate(first,other))
        from unified_reconciliation_policy_v2 import classify_delivery
        self.assertEqual(classify_delivery(first,[other],provider_feed_complete=False),'pending')
        self.assertFalse(is_candidate_duplicate({**first,'metadata':{**first['metadata'],'game':'ats'}},
                                               {**other,'metadata':{**other['metadata'],'game':'ats'}}))


    def test_observed_place_labels_preserved_as_candidates_not_identity(self):
        from unified_job_matching_v2 import _normalized_metadata
        for game,first,last in [('ets2','Newcastle upon Tyne','Newcastle'),('ets2','Зеница (Zenica)','Зеница'),
                                ('ets2','Αθήνα (Athens)','Αθήνα'),('ats','Newyork','New York'),('ats','Toledooh','Toledo')]:
            with self.subTest(game=game,place=first):
                a={**BASE['metadata'],'game':game,'destination':first}
                b={**a,'destination':last}
                self.assertEqual(_normalized_metadata(a),_normalized_metadata(b))
        for first,last in [('Tripoli','Tarabulus'),('Kedmonton','Edmonton')]:
            a={**BASE['metadata'],'destination':first};b={**a,'destination':last}
            self.assertNotEqual(_normalized_metadata(a),_normalized_metadata(b))


if __name__ == "__main__":
    unittest.main()
