import json
import tempfile
import unittest
from pathlib import Path

from seam_survey.facts import load_facts
from seam_survey import RegistrationError

FACTS = Path(__file__).parents[1] / "data/facts.json"


class FactsLoaderTests(unittest.TestCase):
    def test_sketch_loads_and_reports_what_is_missing(self):
        ledger, gaps = load_facts(FACTS)
        # Both zones and their batches register from the sketch.
        self.assertEqual(set(ledger._zones), {"ZONE-E", "ZONE-W"})
        self.assertEqual(set(ledger._batches), {"BATCH-ZONE-E", "BATCH-ZONE-W"})
        # The sketch has no partitions, seam or acceptance record, and its
        # registered responsibility areas do not share the seam.
        joined = " ".join(gaps)
        self.assertIn("seam", joined)
        self.assertIn("ZONE-E and ZONE-W", joined)
        self.assertIn("D-22", joined)

    def test_batches_follow_their_own_network_units(self):
        ledger, _ = load_facts(FACTS)
        e = ledger.diagnose_batch("BATCH-ZONE-E")
        w = ledger.diagnose_batch("BATCH-ZONE-W")
        self.assertTrue(e.coverage_ok)
        self.assertTrue(w.coverage_ok)
        self.assertAlmostEqual(e.transform.scale, 1.0, places=9)
        self.assertAlmostEqual(w.transform.scale, 1.0, places=9)

    def test_gap_between_registered_zones_is_reported_not_papered_over(self):
        ledger, _ = load_facts(FACTS)
        codes = [issue.code for issue in ledger.zone_frame_issues()]
        self.assertIn("zone_responsibility_gap", codes)

    def test_unknown_common_network_is_an_error(self):
        payload = json.loads(FACTS.read_text(encoding="utf-8"))
        payload["common_network"] = "NOPE"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RegistrationError) as ctx:
                load_facts(path)
        self.assertEqual(ctx.exception.code, "missing_common_network")


if __name__ == "__main__":
    unittest.main()
