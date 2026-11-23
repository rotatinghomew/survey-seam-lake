import unittest

from seam_survey import (
    ConflictError,
    ControlNetwork,
    CoverageError,
    RegistrationError,
    ReviewRecord,
    SeamError,
    SurveyBatch,
)

import fixtures as fx


def ledger_with_seam(*, review=True, observations=True):
    ledger = fx.base_ledger()
    if observations:
        ledger.register_batch(
            fx.batch_e(
                observations=[
                    fx.observation(
                        "F-A-01",
                        zone_id="ZONE-E",
                        batch_id="BATCH-A-1",
                        shape=[(9800, 4000), (10020, 4000)],
                        source="SCAN-A-5",
                    )
                ]
            )
        )
        ledger.register_batch(
            fx.batch_w(
                observations=[
                    fx.observation(
                        "F-B-09",
                        zone_id="ZONE-W",
                        batch_id="BATCH-B-1",
                        shape=[(-200, 4000), (300, 4000)],
                        source="SCAN-B-7",
                    )
                ]
            )
        )
    else:
        ledger.register_batch(fx.batch_e())
        ledger.register_batch(fx.batch_w())
    ledger.register_seam(fx.seam_same_object())
    if review:
        ledger.record_review(fx.review_same_object())
    return ledger


class BatchDiagnosticsTests(unittest.TestCase):
    def test_full_coverage_passes_and_reports_areas(self):
        ledger = ledger_with_seam()
        diagnostics = ledger.diagnose_batch("BATCH-A-1")
        self.assertTrue(diagnostics.coverage_ok)
        self.assertAlmostEqual(diagnostics.covered_area_m2, 100.0, places=6)
        self.assertAlmostEqual(diagnostics.gap_area_m2, 0.0, places=6)
        self.assertEqual([issue.code for issue in diagnostics.errors], [])

    def test_late_seam_survey_outside_coverage_is_warning_not_dropped(self):
        ledger = ledger_with_seam()
        diagnostics = ledger.diagnose_batch("BATCH-B-1")
        self.assertTrue(diagnostics.coverage_ok)
        codes = [issue.code for issue in diagnostics.warnings]
        self.assertIn("observation_outside_coverage", codes)
        # The observation is still registered and available to the seam.
        self.assertEqual(ledger.observation("F-B-09").shape[0].x, -200.0)

    def test_short_coverage_is_a_gap(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e(coverage=[(0, 0), (10000, 0), (10000, 9000), (0, 9000)]))
        diagnostics = ledger.diagnose_batch("BATCH-A-1")
        self.assertFalse(diagnostics.coverage_ok)
        self.assertGreater(diagnostics.gap_area_m2, 9.0)
        self.assertIn("coverage_gap", [issue.code for issue in diagnostics.errors])

    def test_unlinked_network_cannot_be_diagnosed_into_coverage(self):
        ledger = fx.base_ledger(link_both=False)
        ledger.register_batch(fx.batch_e())
        diagnostics = ledger.diagnose_batch("BATCH-A-1")
        self.assertFalse(diagnostics.coverage_ok)
        self.assertIsNone(diagnostics.coverage)
        self.assertIn("unlinked_network", [issue.code for issue in diagnostics.errors])

    def test_declared_unit_mismatch_is_not_silently_repaired(self):
        # The batch declares metres, but its coordinates are millimetre
        # magnitudes.  The ledger must not guess the "real" unit and rescale:
        # the delivered coverage is enormous and is reported as overhang.
        ledger = fx.base_ledger()
        metric = ControlNetwork(
            id="LOCAL-A-M",
            crs="LOCAL-A",
            unit="m",
            version=1,
            points={"CP-1": (1000, 1000), "CP-2": (1010, 1000), "CP-3": (1000, 1010)},
        )
        ledger.register_network(metric)
        ledger.link_networks("LINK-AM", ("LOCAL-A-M", 1), ("SITE", 2))
        ledger.register_batch(
            SurveyBatch(
                id="BATCH-WRONG-UNIT",
                zone_id="ZONE-E",
                network_id="LOCAL-A-M",
                network_version=1,
                coverage=tuple(fx.ZONE_E_COVERAGE_MM),
            )
        )
        diagnostics = ledger.diagnose_batch("BATCH-WRONG-UNIT")
        self.assertAlmostEqual(diagnostics.coverage_area_m2, 1.0e8, places=0)
        self.assertIn("coverage_overhang", [issue.code for issue in diagnostics.warnings])


class SeamAssessmentTests(unittest.TestCase):
    def test_open_seam_blocks_counting(self):
        ledger = ledger_with_seam(review=False)
        assessment = ledger.assess_seam("SEAM-1")
        self.assertEqual(assessment.status, "open")
        self.assertIn("seam_open", [issue.code for issue in assessment.errors])

    def test_same_object_requires_primary_and_comparison(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(observations=[fx.observation("F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)])])
        )
        ledger.register_batch(
            fx.batch_w(observations=[fx.observation("F-B-09", zone_id="ZONE-W", batch_id="BATCH-B-1", shape=[(-200, 4000), (300, 4000)])])
        )
        ledger.register_seam(fx.seam_same_object())
        ledger.record_review(
            ReviewRecord(
                id="REV-NOCOMP",
                subject="SEAM-1",
                decision="same_object",
                seam_id="SEAM-1",
                primary_id="F-A-01",
                comparison_ids=(),
            )
        )
        assessment = ledger.assess_seam("SEAM-1")
        self.assertIn("missing_comparison", [issue.code for issue in assessment.errors])

    def test_same_object_comparison_must_match_position(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(observations=[fx.observation("F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)])])
        )
        # A comparison far away cannot corroborate the primary.
        ledger.register_batch(
            fx.batch_w(observations=[fx.observation("F-B-09", zone_id="ZONE-W", batch_id="BATCH-B-1", shape=[(5000, 5000), (5300, 5000)])])
        )
        ledger.register_seam(fx.seam_same_object())
        ledger.record_review(fx.review_same_object())
        assessment = ledger.assess_seam("SEAM-1")
        self.assertIn("comparison_not_comparable", [issue.code for issue in assessment.errors])
        self.assertFalse(assessment.ready)

    def test_two_objects_requires_both_zones(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(observations=[fx.observation("F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)])])
        )
        ledger.register_batch(fx.batch_w())
        with self.assertRaises(RegistrationError) as ctx:
            ledger.register_seam(fx.seam_same_object(("F-A-01",)))
        self.assertEqual(ctx.exception.code, "seam_missing_side")

    def test_two_objects_keeps_both_counts(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(observations=[fx.observation("F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)])])
        )
        ledger.register_batch(
            fx.batch_w(observations=[fx.observation("F-B-09", zone_id="ZONE-W", batch_id="BATCH-B-1", shape=[(-200, 4000), (300, 4000)])])
        )
        ledger.register_seam(fx.seam_same_object())
        ledger.record_review(
            ReviewRecord(
                id="REV-2",
                subject="SEAM-1",
                decision="two_objects",
                seam_id="SEAM-1",
            )
        )
        assessment = ledger.assess_seam("SEAM-1")
        self.assertEqual(assessment.decision, "two_objects")
        self.assertTrue(assessment.ready)
        partition_e = ledger.build_partition("BATCH-A-1")
        partition_w = ledger.build_partition("BATCH-B-1")
        roles = {
            feature.observation_id: (feature.role, feature.counted)
            for feature in partition_e.features + partition_w.features
        }
        self.assertEqual(roles["F-A-01"], ("separate", True))
        self.assertEqual(roles["F-B-09"], ("separate", True))

    def test_seam_without_registered_link_is_blocked(self):
        ledger = fx.base_ledger(link_both=False)
        ledger.register_batch(
            fx.batch_e(observations=[fx.observation("F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)])])
        )
        ledger.register_batch(
            fx.batch_w(observations=[fx.observation("F-B-09", zone_id="ZONE-W", batch_id="BATCH-B-1", shape=[(-200, 4000), (300, 4000)])])
        )
        ledger.register_seam(fx.seam_same_object())
        ledger.record_review(fx.review_same_object())
        assessment = ledger.assess_seam("SEAM-1")
        self.assertFalse(assessment.control_ok)
        self.assertIn("seam_control_unlinked", [issue.code for issue in assessment.errors])

    def test_seam_must_register_both_sides(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(observations=[fx.observation("F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)])])
        )
        with self.assertRaises(RegistrationError) as ctx:
            ledger.register_seam(fx.seam_same_object(("F-A-01",)))
        self.assertEqual(ctx.exception.code, "seam_missing_side")

    def test_reviews_are_append_only_and_reenable_processing(self):
        ledger = ledger_with_seam(review=False)
        first = ledger.record_review(
            ReviewRecord(
                id="REV-OPEN",
                subject="SEAM-1",
                decision="unresolved",
                seam_id="SEAM-1",
            )
        )
        self.assertEqual(ledger.seam("SEAM-1").status, "blocked")
        with self.assertRaises(ConflictError):
            ledger.record_review(first)
        ledger.record_review(fx.review_same_object(review_id="REV-CLOSE"))
        assessment = ledger.assess_seam("SEAM-1")
        self.assertEqual(assessment.status, "reviewed")
        self.assertTrue(assessment.ready)


class PartitionLifecycleTests(unittest.TestCase):
    def test_unreviewed_seam_cannot_be_delivered(self):
        ledger = ledger_with_seam(review=False)
        partition = ledger.build_partition("BATCH-A-1")
        self.assertEqual([f.role for f in partition.features], ["pending"])
        with self.assertRaises(SeamError) as ctx:
            ledger.deliver_partition(partition.id)
        self.assertEqual(ctx.exception.code, "partition_blocked")

    def test_review_later_unblocks_existing_draft(self):
        ledger = ledger_with_seam(review=False)
        partition = ledger.build_partition("BATCH-A-1")
        ledger.record_review(fx.review_same_object())
        readiness = ledger.partition_readiness(partition.id)
        self.assertTrue(readiness.ready, readiness.to_dict())
        # Still the same immutable snapshot; a fresh draft carries the review.
        refreshed = ledger.refresh_draft(partition.id)
        delivered = ledger.deliver_partition(refreshed.id)
        self.assertEqual(delivered.status, "delivered")

    def test_delivered_partition_cannot_be_refreshed_in_place(self):
        ledger = ledger_with_seam()
        partition = ledger.build_partition("BATCH-A-1")
        ledger.deliver_partition(partition.id)
        with self.assertRaises(SeamError) as ctx:
            ledger.refresh_draft(partition.id)
        self.assertEqual(ctx.exception.code, "partition_locked")

    def test_delivered_partition_still_readable_after_replacement(self):
        ledger = ledger_with_seam()
        partition = ledger.build_partition("BATCH-A-1")
        ledger.deliver_partition(partition.id)
        original_coverage = partition.coverage
        replacement = ledger.replace_partition(partition.id)
        self.assertEqual(replacement.replaces, partition.id)
        self.assertEqual(ledger.partition(partition.id).status, "delivered")
        self.assertEqual(ledger.partition(partition.id).coverage, original_coverage)

    def test_coverage_gap_draft_is_not_deliverable(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e(coverage=[(0, 0), (10000, 0), (10000, 9000), (0, 9000)]))
        partition = ledger.build_partition("BATCH-A-1")
        self.assertFalse(partition.coverage_ok)
        with self.assertRaises(SeamError):
            ledger.deliver_partition(partition.id)

    def test_unlinked_batch_cannot_become_a_partition(self):
        ledger = fx.base_ledger(link_both=False)
        ledger.register_batch(fx.batch_e())
        with self.assertRaises(CoverageError) as ctx:
            ledger.build_partition("BATCH-A-1")
        self.assertEqual(ctx.exception.code, "unlinked_batch")

    def test_traceability_keeps_raw_measurement_and_transform(self):
        ledger = ledger_with_seam()
        partition = ledger.build_partition("BATCH-A-1")
        trace = ledger.trace_partition(partition.id)
        self.assertEqual(trace["source_network"]["unit"], "mm")
        self.assertEqual(trace["common_network"]["unit"], "m")
        self.assertEqual(trace["transform"]["link_ids"], ["LINK-A"])
        raw = trace["observations"][0]["raw_observation"]
        self.assertEqual(raw["shape"][0], [9800.0, 4000.0])
        # The partition geometry is in metres in the common frame.
        partition_shape = trace["observations"][0]["partition_feature"]["shape"]
        self.assertAlmostEqual(partition_shape[0][0], 1009.8, places=6)


class DuplicateGapTests(unittest.TestCase):
    def test_pending_duplicate_cannot_close_a_gap(self):
        ledger = fx.base_ledger()
        # Zone E batch covers only up to x = 9.98 m: a 0.02 m strip is missing,
        # and the only feature sitting in it belongs to an unresolved seam.
        ledger.register_batch(
            fx.batch_e(
                coverage=[(0, 0), (9980, 0), (9980, 10000), (0, 10000)],
                observations=[
                    fx.observation(
                        "F-A-01",
                        zone_id="ZONE-E",
                        batch_id="BATCH-A-1",
                        shape=[(9800, 4000), (10020, 4000)],
                    )
                ],
            )
        )
        ledger.register_batch(
            fx.batch_w(
                observations=[
                    fx.observation(
                        "F-B-09",
                        zone_id="ZONE-W",
                        batch_id="BATCH-B-1",
                        shape=[(-200, 4000), (300, 4000)],
                    )
                ]
            )
        )
        ledger.register_seam(fx.seam_same_object())
        partition = ledger.build_partition("BATCH-A-1")
        self.assertFalse(partition.coverage_ok)
        codes = [issue.code for issue in partition.issues]
        self.assertIn("duplicate_points_fill_gap", codes)
        self.assertIn("seam_unresolved", codes)


class HandoverTests(unittest.TestCase):
    def _deliver_pair(self):
        ledger = ledger_with_seam()
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        return ledger, pe, pw

    def test_clean_adjacent_handover_succeeds(self):
        ledger, pe, pw = self._deliver_pair()
        report = ledger.handover([pe.id, pw.id])
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(report.counted_feature_ids, ("F-A-01",))
        self.assertEqual(len(report.pair_checks), 1)
        self.assertTrue(report.pair_checks[0].control_ok)

    def test_undelivered_partition_blocks_handover(self):
        ledger = ledger_with_seam()
        pe = ledger.build_partition("BATCH-A-1")
        with self.assertRaises(SeamError) as ctx:
            ledger.pin_design("D-22", [pe.id], edition="E1")
        self.assertEqual(ctx.exception.code, "handover_blocked")

    def test_old_and_new_partition_for_same_zone_cannot_both_count(self):
        ledger, pe, pw = self._deliver_pair()
        rebuilt = ledger.replace_partition(pe.id)
        ledger.deliver_partition(rebuilt.id)
        report = ledger.handover([pe.id, rebuilt.id, pw.id])
        self.assertFalse(report.ok)
        self.assertIn("duplicate_zone_partition", [issue.code for issue in report.errors])

    def test_new_and_old_measuring_different_halves_makes_a_gap(self):
        ledger = fx.base_ledger()
        # Old batch covers the west half only; new batch covers the east half.
        ledger.register_batch(
            fx.batch_e(
                batch_id="BATCH-A-OLD",
                coverage=[(0, 0), (5000, 0), (5000, 10000), (0, 10000)],
            )
        )
        ledger.register_batch(
            fx.batch_e(
                batch_id="BATCH-A-NEW",
                coverage=[(5000, 0), (10000, 0), (10000, 10000), (5000, 10000)],
            )
        )
        old = ledger.build_partition("BATCH-A-OLD", partition_id="P-OLD")
        new = ledger.build_partition("BATCH-A-NEW", partition_id="P-NEW")
        report = ledger.handover(["P-OLD", "P-NEW"], require_delivered=False)
        self.assertFalse(report.ok)
        codes = [issue.code for issue in report.errors]
        self.assertIn("duplicate_zone_partition", codes)

    def test_overlapping_feature_without_review_blocks_handover(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(
                observations=[
                    fx.observation(
                        "F-A-X",
                        zone_id="ZONE-E",
                        batch_id="BATCH-A-1",
                        shape=[(9800, 4000), (10020, 4000)],
                    )
                ]
            )
        )
        ledger.register_batch(
            fx.batch_w(
                observations=[
                    fx.observation(
                        "F-B-X",
                        zone_id="ZONE-W",
                        batch_id="BATCH-B-1",
                        shape=[(-200, 4000), (300, 4000)],
                    )
                ]
            )
        )
        pe = ledger.build_partition("BATCH-A-1")
        pw = ledger.build_partition("BATCH-B-1")
        report = ledger.handover([pe.id, pw.id], require_delivered=False)
        self.assertFalse(report.ok)
        self.assertIn("unregistered_overlap", [issue.code for issue in report.errors])

    def test_reviewed_two_objects_allows_overlapping_features(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(
                observations=[
                    fx.observation(
                        "F-A-X", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)]
                    )
                ]
            )
        )
        ledger.register_batch(
            fx.batch_w(
                observations=[
                    fx.observation(
                        "F-B-X", zone_id="ZONE-W", batch_id="BATCH-B-1", shape=[(-200, 4000), (300, 4000)]
                    )
                ]
            )
        )
        ledger.register_seam(
            fx.SeamGroup(
                id="SEAM-X",
                zone_a="ZONE-E",
                zone_b="ZONE-W",
                observation_ids=("F-A-X", "F-B-X"),
            )
        )
        ledger.record_review(
            ReviewRecord(
                id="REV-X",
                subject="SEAM-X",
                decision="two_objects",
                seam_id="SEAM-X",
            )
        )
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        report = ledger.handover([pe.id, pw.id])
        self.assertTrue(report.ok, report.to_dict())

    def test_each_zone_passes_but_combined_seam_has_a_gap(self):
        """Per-zone tolerance can let both sides pass while the seam is torn."""
        ledger = fx.base_ledger()
        ledger.coverage_tolerance_m2 = 1.0
        # E stops 50 mm short of the seam, W starts 50 mm past it.
        ledger.register_batch(
            fx.batch_e(coverage=[(0, 0), (9950, 0), (9950, 10000), (0, 10000)])
        )
        ledger.register_batch(
            fx.batch_w(coverage=[(50, 0), (10000, 0), (10000, 10000), (50, 10000)])
        )
        self.assertTrue(ledger.diagnose_batch("BATCH-A-1").coverage_ok)
        self.assertTrue(ledger.diagnose_batch("BATCH-B-1").coverage_ok)
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        report = ledger.handover([pe.id, pw.id])
        self.assertFalse(report.ok)
        self.assertIn("seam_coverage_separated", [issue.code for issue in report.errors])
        self.assertAlmostEqual(report.pair_checks[0].seam_distance_m, 0.1, places=6)


class ReplacementTests(unittest.TestCase):
    def test_replacement_plan_names_affected_seams_and_candidates(self):
        ledger = ledger_with_seam()
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        plan = ledger.replacement_plan([pe.id, pw.id])
        self.assertEqual(plan["affected_seams"], ["SEAM-1"])
        self.assertTrue(plan["can_replace_together"])
        self.assertEqual(len(plan["candidate_ids"]), 2)
        self.assertEqual(plan["group_issues"], [])

    def test_replacement_plan_reports_pinned_drawings(self):
        ledger = ledger_with_seam()
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        ledger.pin_design("D-22", [pe.id, pw.id], edition="E3")
        plan = ledger.replacement_plan([pe.id, pw.id])
        self.assertEqual(plan["affected_drawings"], ["D-22"])
        # One side of the pair alone would omit the seam's other zone, so the
        # grouped replacement is the only jointly acceptable option.
        partial = ledger.replacement_plan([pe.id])
        self.assertIn("SEAM-1", partial["affected_seams"])

    def test_pinned_partition_keeps_its_historical_coverage(self):
        ledger, pe, pw = HandoverTests()._deliver_pair()
        reference = ledger.pin_design("D-22", [pe.id, pw.id], edition="E3")
        self.assertTrue(ledger.is_referenced(pe.id))
        historical = ledger.partition(pe.id)
        self.assertEqual(historical.edition, reference.edition_of(pe.id))
        self.assertEqual(historical.coverage, pe.coverage)

    def test_zone_without_seam_feature_is_unaffected_by_open_seam(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(observations=[fx.observation("F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)])])
        )
        ledger.register_batch(
            fx.batch_w(observations=[fx.observation("F-B-09", zone_id="ZONE-W", batch_id="BATCH-B-1", shape=[(-200, 4000), (300, 4000)])])
        )
        ledger.register_seam(fx.seam_same_object())
        # Both E and W carry a feature in the open seam, so both drafts block.
        pe = ledger.build_partition("BATCH-A-1")
        pw = ledger.build_partition("BATCH-B-1")
        with self.assertRaises(SeamError):
            ledger.deliver_partition(pe.id)
        with self.assertRaises(SeamError):
            ledger.deliver_partition(pw.id)
        # An unrelated zone with no seam feature is unaffected.
        ledger.register_zone(fx.zone_east_extension())
        ledger.register_batch(
            SurveyBatch(
                id="BATCH-W2",
                zone_id="ZONE-W2",
                network_id="LOCAL-B",
                network_version=1,
                coverage=fx.ZONE_W2_COVERAGE_MM,
            )
        )
        p2 = ledger.deliver_partition(ledger.build_partition("BATCH-W2").id)
        self.assertEqual(p2.status, "delivered")

    def test_review_after_gap_allows_continuation(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(
                coverage=[(0, 0), (9980, 0), (9980, 10000), (0, 10000)],
                observations=[
                    fx.observation("F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(9800, 4000), (10020, 4000)])
                ],
            )
        )
        ledger.register_batch(
            fx.batch_w(observations=[fx.observation("F-B-09", zone_id="ZONE-W", batch_id="BATCH-B-1", shape=[(-200, 4000), (300, 4000)])])
        )
        ledger.register_seam(fx.seam_same_object())
        blocked = ledger.build_partition("BATCH-A-1")
        self.assertFalse(blocked.coverage_ok)
        # The acceptance sheet arrives later; it resolves the seam but cannot
        # manufacture the missing 0.2 m strip of coverage.
        ledger.record_review(fx.review_same_object())
        self.assertFalse(ledger.partition_readiness(blocked.id).ready)
        # A corrected redelivery of the same zone can now be delivered.
        ledger.register_batch(
            fx.batch_e(
                batch_id="BATCH-A-2",
                observations=[
                    fx.observation("F-A-02", zone_id="ZONE-E", batch_id="BATCH-A-2", shape=[(9800, 4000), (10020, 4000)])
                ],
            )
        )
        fixed = ledger.build_partition("BATCH-A-2")
        self.assertTrue(fixed.coverage_ok)
        delivered = ledger.deliver_partition(fixed.id)
        self.assertEqual(delivered.status, "delivered")


if __name__ == "__main__":
    unittest.main()
