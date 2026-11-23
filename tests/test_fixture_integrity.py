import json
from pathlib import Path
import unittest


class FixtureIntegrityTests(unittest.TestCase):
    def test_adjacent_zones_and_observations_are_present(self):
        data = json.loads((Path(__file__).parents[1] / "data/facts.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["control_networks"]), 3)
        self.assertEqual({row["unit"] for row in data["control_networks"]}, {"mm", "m"})
        self.assertEqual({row["id"] for row in data["zones"]}, {"ZONE-E", "ZONE-W"})
        self.assertEqual({row["zone"] for row in data["feature_observations"]}, {"ZONE-E", "ZONE-W"})


if __name__ == "__main__":
    unittest.main()
