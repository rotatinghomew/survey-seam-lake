import unittest

from seam_survey import (
    ControlNetwork,
    RegistrationError,
    ToleranceError,
    Zone,
)

import fixtures as fx


class GeometryHelperTests(unittest.TestCase):
    def test_convex_difference_partitions_area(self):
        from seam_survey.geometry import convex_difference, polygon_area

        subject = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        clip = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]
        pieces = convex_difference(subject, clip)
        self.assertGreaterEqual(len(pieces), 1)
        total = sum(polygon_area(piece) for piece in pieces)
        self.assertAlmostEqual(total, 96.0, places=6)

    def test_union_area_handles_overlap(self):
        from seam_survey.geometry import polygon_union_area

        a = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        b = [(5.0, 0.0), (15.0, 0.0), (15.0, 10.0), (5.0, 10.0)]
        self.assertAlmostEqual(polygon_union_area([a, b]), 150.0, places=6)

    def test_non_convex_zone_rejected(self):
        with self.assertRaises(RegistrationError) as ctx:
            Zone(
                id="Z",
                owner="T",
                responsibility=[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (5.0, 5.0), (0.0, 10.0)],
            )
        self.assertEqual(ctx.exception.code, "non_convex_zone")

    def test_unknown_unit_rejected(self):
        with self.assertRaises(RegistrationError) as ctx:
            ControlNetwork(id="N", crs="C", unit="ft", version=1, points={"P": (0, 0)})
        self.assertEqual(ctx.exception.code, "unsupported_unit")


class TransformTests(unittest.TestCase):
    def test_millimetres_to_metres_with_offset(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        record = ledger.transform_record("BATCH-A-1")
        self.assertAlmostEqual(record.scale, 1.0, places=9)
        self.assertAlmostEqual(record.source_unit, "mm")
        self.assertEqual(record.target_unit, "m")
        self.assertAlmostEqual(record.max_residual_m, 0.0, places=9)


class LinkRegistrationTests(unittest.TestCase):
    def test_link_uses_registered_codes_only(self):
        ledger = fx.base_ledger(link_both=False)
        # LOCAL-A and LOCAL-B share no registered point codes; a link must not
        # be guessed geometrically.
        with self.assertRaises(RegistrationError) as ctx:
            ledger.link_networks("X", ("LOCAL-A", 1), ("LOCAL-B", 1))
        self.assertEqual(ctx.exception.code, "insufficient_common_points")

    def test_too_few_common_points_rejected(self):
        ledger = fx.base_ledger(link_both=False)
        sparse = ControlNetwork(
            id="SPARSE", crs="S", unit="m", version=1, points={"CP-1": (1000, 1000), "CP-2": (1010, 1000)}
        )
        ledger.register_network(sparse)
        with self.assertRaises(RegistrationError) as ctx:
            ledger.link_networks("S", ("SPARSE", 1), ("SITE", 2))
        self.assertEqual(ctx.exception.code, "insufficient_common_points")

    def test_out_of_tolerance_link_rejected(self):
        ledger = fx.base_ledger(link_both=False)
        site = ControlNetwork(
            id="SITE-SKEW",
            crs="SITE",
            unit="m",
            version=1,
            points={
                "CP-1": (1000.0, 1000.0),
                "CP-2": (1010.08, 1000.0),
                "CP-3": (1000.0, 1009.95),
            },
        )
        ledger.register_network(site)
        with self.assertRaises(ToleranceError) as ctx:
            ledger.link_networks("BAD", ("LOCAL-A", 1), ("SITE-SKEW", 1))
        self.assertEqual(ctx.exception.code, "residual_exceeded")
        self.assertIn("residuals_m", ctx.exception.details)

    def test_scale_conflict_across_units(self):
        ledger = fx.base_ledger(link_both=False)
        # Says millimetres, is actually centimetres: a 10x scale conflict.
        wrong = ControlNetwork(
            id="LOCAL-C",
            crs="LOCAL-C",
            unit="mm",
            version=1,
            points={"CP-1": (0, 0), "CP-2": (1000, 0), "CP-3": (0, 1000)},
        )
        ledger.register_network(wrong)
        with self.assertRaises(ToleranceError) as ctx:
            ledger.link_networks("BADSCALE", ("LOCAL-C", 1), ("SITE", 2))
        self.assertEqual(ctx.exception.code, "scale_conflict")

    def test_duplicate_link_direction_rejected(self):
        ledger = fx.base_ledger()
        with self.assertRaises(RegistrationError) as ctx:
            ledger.link_networks("DUP", ("LOCAL-A", 1), ("SITE", 2))
        self.assertEqual(ctx.exception.code, "duplicate_link")

    def test_network_version_must_advance(self):
        ledger = fx.base_ledger()
        with self.assertRaises(RegistrationError) as ctx:
            ledger.register_network(
                ControlNetwork(id="SITE", crs="SITE", unit="m", version=1, points={"CP-1": (0, 0)})
            )
        self.assertEqual(ctx.exception.code, "version_out_of_order")

    def test_derived_link_between_local_networks_is_auditable(self):
        ledger = fx.base_ledger()
        lineage = ledger.link_lineage(("LOCAL-A", 1), ("LOCAL-B", 1))
        self.assertFalse(lineage["identity"])
        self.assertEqual([item["id"] for item in lineage["links"]], ["LINK-A", "LINK-B"])
        # LOCAL-A's origin lies 10 m west of LOCAL-B's origin, so in LOCAL-B's
        # frame it projects to x = -10 m.
        self.assertAlmostEqual(lineage["transform"]["translation_m"][0], -10.0, places=6)


if __name__ == "__main__":
    unittest.main()
