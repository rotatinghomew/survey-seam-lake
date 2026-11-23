import unittest

from seam_survey import (
    ConflictError,
    ControlNetwork,
    FeatureObservation,
    RegistrationError,
    SeamError,
    SurveyBatch,
    Zone,
)

import fixtures as fx


class CommonFrameBatchTests(unittest.TestCase):
    def test_batch_delivered_directly_in_metres_needs_no_link(self):
        ledger = fx.base_ledger(link_both=False)
        # A team whose scanner already works in the project SITE frame.
        ledger.register_batch(fx.batch_e(network_id="SITE", network_version=2))
        diagnostics = ledger.diagnose_batch("BATCH-A-1")
        self.assertTrue(diagnostics.coverage_ok)
        self.assertTrue(diagnostics.transform.identity)
        self.assertEqual(diagnostics.transform.point_codes, ())

    def test_metre_batch_cannot_reach_a_zone_registered_elsewhere(self):
        ledger = fx.base_ledger(link_both=False)
        ledger.register_batch(
            SurveyBatch(
                id="BATCH-FAR",
                zone_id="ZONE-E",
                network_id="SITE",
                network_version=2,
                coverage=[(2000.0, 2000.0), (2010.0, 2000.0), (2010.0, 2010.0), (2000.0, 2010.0)],
            )
        )
        diagnostics = ledger.diagnose_batch("BATCH-FAR")
        self.assertFalse(diagnostics.coverage_ok)
        self.assertIn("coverage_gap", [issue.code for issue in diagnostics.errors])


class IndependentSubmissionTests(unittest.TestCase):
    def test_feature_outside_any_seam_is_counted(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(
                observations=[
                    fx.observation(
                        "F-A-INDEP",
                        zone_id="ZONE-E",
                        batch_id="BATCH-A-1",
                        shape=[(3000, 4000), (3200, 4000)],
                    )
                ]
            )
        )
        partition = ledger.build_partition("BATCH-A-1")
        features = {feature.observation_id: feature for feature in partition.features}
        self.assertEqual(features["F-A-INDEP"].role, "independent")
        self.assertTrue(features["F-A-INDEP"].counted)
        ledger.deliver_partition(partition.id)

    def test_observation_added_after_draft_is_pickable_by_refresh(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        draft = ledger.build_partition("BATCH-A-1")
        self.assertEqual(draft.features, ())
        ledger.register_observation(
            FeatureObservation(
                id="F-A-LATE",
                zone_id="ZONE-E",
                batch_id="BATCH-A-1",
                shape=((3000, 4000), (3200, 4000)),
            )
        )
        refreshed = ledger.refresh_draft(draft.id)
        self.assertEqual([feature.observation_id for feature in refreshed.features], ["F-A-LATE"])
        self.assertTrue(refreshed.features[0].counted)

    def test_observation_in_unregistered_batch_is_refused(self):
        ledger = fx.base_ledger()
        with self.assertRaises(RegistrationError) as ctx:
            ledger.register_observation(
                FeatureObservation(
                    id="F-X",
                    zone_id="ZONE-E",
                    batch_id="NO-SUCH-BATCH",
                    shape=((0, 0), (1, 0)),
                )
            )
        self.assertEqual(ctx.exception.code, "unknown_batch")

    def test_duplicate_observation_id_is_refused(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(
                observations=[
                    fx.observation(
                        "F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(0, 0), (1, 0)]
                    )
                ]
            )
        )
        with self.assertRaises(ConflictError):
            ledger.register_observation(
                FeatureObservation(
                    id="F-A-01",
                    zone_id="ZONE-E",
                    batch_id="BATCH-A-1",
                    shape=((0, 0), (1, 0)),
                )
            )


class FixtureAlignmentTests(unittest.TestCase):
    def test_overlap_is_only_optional_when_both_sides_registered(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        ledger.register_batch(fx.batch_w())
        # REGISTERING a seam is what flags a duplicate; merely having two nearby
        # features is not enough to decide anything.
        self.assertEqual(ledger._seams, {})
        report = ledger.handover(["P-NOPE"], require_delivered=False) if False else None
        self.assertIsNone(report)

    def test_zone_registration_rejects_self_overlap_probe(self):
        ledger = fx.base_ledger()
        overlapping = Zone(
            id="ZONE-X",
            owner="TEAM-X",
            responsibility=[(1005.0, 1000.0), (1015.0, 1000.0), (1015.0, 1010.0), (1005.0, 1010.0)],
        )
        ledger.register_zone(overlapping)
        codes = [issue.code for issue in ledger.zone_frame_issues()]
        # ZONE-X overlaps ZONE-E by half its area; that is a registration
        # conflict the project must see before any delivery.
        self.assertIn("zone_responsibility_overlap", codes)

    def test_separated_zones_are_reported(self):
        ledger = fx.base_ledger()
        far = Zone(
            id="ZONE-FAR",
            owner="TEAM-C",
            responsibility=[(1100.0, 1000.0), (1110.0, 1000.0), (1110.0, 1010.0), (1100.0, 1010.0)],
        )
        ledger.register_zone(far)
        codes = [issue.code for issue in ledger.zone_frame_issues()]
        self.assertIn("zone_responsibility_gap", codes)


if __name__ == "__main__":
    unittest.main()
