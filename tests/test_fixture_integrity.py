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

    def test_fixture_observations_straddle_the_seam(self):
        data = json.loads((Path(__file__).parents[1] / "data/facts.json").read_text(encoding="utf-8"))
        by_zone = {row["id"]: row for row in data["zones"]}
        # ZONE-E ends at 10 m of its own local frame; its observation reaches
        # 20 mm past that edge.  Both observations therefore poke toward the
        # other team's side of the seam.
        east = next(row for row in data["feature_observations"] if row["zone"] == "ZONE-E")
        west = next(row for row in data["feature_observations"] if row["zone"] == "ZONE-W")
        self.assertGreater(east["shape"][1][0], by_zone["ZONE-E"]["end_m"] * 1000)
        self.assertGreater(west["shape"][1][0], by_zone["ZONE-E"]["end_m"] * 1000 - 10000)


if __name__ == "__main__":
    unittest.main()
