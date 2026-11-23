import json
import subprocess
import sys
import unittest
from pathlib import Path

FACTS = Path(__file__).parents[1] / "data/facts.json"
ROOT = Path(__file__).parents[1]


class CliTests(unittest.TestCase):
    def test_human_output_names_batches_and_frame_issue(self):
        result = subprocess.run(
            [sys.executable, "-m", "seam_survey.cli", str(FACTS)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BATCH-ZONE-E", result.stdout)
        self.assertIn("zone_responsibility_gap", result.stdout)
        self.assertIn("sketch gaps", result.stdout)

    def test_json_output_is_parseable(self):
        result = subprocess.run(
            [sys.executable, "-m", "seam_survey.cli", str(FACTS), "--json"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["project"]["common_network"], "NET-SITE-2@2")
        self.assertIn("BATCH-ZONE-E", payload["diagnostics"])
        self.assertTrue(payload["gaps"])

    def test_missing_file_is_reported(self):
        result = subprocess.run(
            [sys.executable, "-m", "seam_survey.cli", "no/such/facts.json"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
