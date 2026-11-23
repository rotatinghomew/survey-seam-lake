import unittest

from seam_survey import (
    ConflictError,
    ControlNetwork,
    RegistrationError,
    SeamError,
    SurveyBatch,
)

import fixtures as fx


def delivered_pair(ledger):
    pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
    pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
    return pe, pw


class ControlVersionTests(unittest.TestCase):
    def test_new_version_is_applicable_but_does_not_retire_old(self):
        ledger = fx.base_ledger(link_both=False)
        ledger.register_network(fx.site_network(version=3))
        # Registering a later round must not silently invalidate work already
        # tied to the earlier version.
        self.assertEqual(ledger.network("SITE", 2).status, "active")
        self.assertEqual(ledger.network("SITE", 3).status, "active")
        self.assertEqual(ledger.applicable_version("SITE"), 3)
        # Old point coordinates remain readable for audit.
        self.assertEqual(ledger.network("SITE", 2).points["CP-1"], (1000.0, 1000.0))

    def test_explicit_supersede_retires_old_version(self):
        ledger = fx.base_ledger(link_both=False)
        ledger.register_network(fx.site_network(version=3))
        ledger.supersede_network("SITE", 2, note="v3 adopted")
        self.assertEqual(ledger.network("SITE", 2).status, "superseded")
        self.assertEqual(ledger.network("SITE", 3).status, "active")

    def test_superseded_version_is_flagged_on_batch(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        ledger.register_network(fx.site_network(version=3))
        # Flagging only happens once the old version is actually withdrawn.
        self.assertNotIn(
            "superseded_control_version",
            [issue.code for issue in ledger.diagnose_batch("BATCH-A-1").issues],
        )
        ledger.supersede_network("SITE", 2)
        codes = [issue.code for issue in ledger.diagnose_batch("BATCH-A-1").issues]
        self.assertIn("superseded_control_version", codes)

    def test_new_partition_cannot_be_cut_on_superseded_basis(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        ledger.register_network(fx.site_network(version=3))
        ledger.supersede_network("SITE", 2)
        partition = ledger.build_partition("BATCH-A-1")
        # A draft still exists so the problem is inspectable, but it cannot be
        # signed off against an out-of-date control basis.
        self.assertTrue(partition.coverage_ok)
        readiness = ledger.partition_readiness(partition.id)
        self.assertFalse(readiness.ready)
        self.assertIn("superseded_control_basis", [issue.code for issue in readiness.errors])
        with self.assertRaises(SeamError):
            ledger.deliver_partition(partition.id)

    def test_reassociate_batch_to_new_version_keeps_raw_coordinates(self):
        ledger = fx.base_ledger()
        ledger.register_network(
            ControlNetwork(
                id="LOCAL-A-R",
                crs="LOCAL-A",
                unit="mm",
                version=1,
                points={"CP-1": (0, 0), "CP-2": (10000, 0), "CP-3": (0, 10000)},
            )
        )
        ledger.link_networks("LINK-A-R", ("LOCAL-A-R", 1), ("SITE", 2))
        ledger.register_batch(fx.batch_e())
        ledger.reassign_batch_network("BATCH-A-1", "LOCAL-A-R", 1)
        batch = ledger.batch("BATCH-A-1")
        self.assertEqual((batch.network_id, batch.network_version), ("LOCAL-A-R", 1))
        self.assertEqual(batch.coverage[0].as_tuple(), (0.0, 0.0))
        self.assertEqual([i.code for i in ledger.diagnose_batch("BATCH-A-1").issues], [])

    def test_reassociate_requires_a_link_to_common_frame(self):
        ledger = fx.base_ledger()
        ledger.register_network(
            ControlNetwork(
                id="LOCAL-ORPHAN",
                crs="X",
                unit="mm",
                version=1,
                points={"CP-1": (0, 0), "CP-2": (10000, 0), "CP-3": (0, 10000)},
            )
        )
        with self.assertRaises(RegistrationError) as ctx:
            ledger.reassign_batch_network("BATCH-A-1", "LOCAL-ORPHAN", 1)
        self.assertEqual(ctx.exception.code, "unknown_batch")

    def test_reissued_link_changes_handover_verdict(self):
        """A later control result that tears the seam must block handover."""
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        ledger.register_batch(fx.batch_w())
        pe, pw = delivered_pair(ledger)
        self.assertTrue(ledger.handover([pe.id, pw.id]).ok)

        # The control office re-issues SITE with TEAM-B's stations moved east.
        # New partitions for TEAM-B are projected through the new basis while
        # TEAM-A's older snapshots still refer to the old one.
        ledger.register_network(fx.site_network(x_shift=0.5, version=3))
        ledger.register_network(
            ControlNetwork(
                id="LOCAL-B-3",
                crs="LOCAL-B",
                unit="mm",
                version=1,
                points={"CP-4": (0, 0), "CP-5": (10000, 0), "CP-6": (0, 10000)},
            )
        )
        ledger.link_networks("LINK-B-3", ("LOCAL-B-3", 1), ("SITE", 3))
        ledger.link_networks("LINK-A-3", ("LOCAL-A", 1), ("SITE", 3))
        ledger.link_networks("LINK-B-V23", ("LOCAL-B", 1), ("SITE", 3))
        ledger.advance_common_network("SITE", 3)
        ledger.register_batch(
            SurveyBatch(
                id="BATCH-B-3",
                zone_id="ZONE-W",
                network_id="LOCAL-B-3",
                network_version=1,
                coverage=tuple(fx.ZONE_W_COVERAGE_MM),
            )
        )
        new_w = ledger.build_partition("BATCH-B-3")
        # On the new basis this delivery no longer lands squarely in ZONE-W,
        # so the ledger flags it instead of stretching it to fit.
        self.assertFalse(new_w.coverage_ok)
        self.assertIn("coverage_gap", [issue.code for issue in new_w.errors])
        report = ledger.handover([pe.id, new_w.id], require_delivered=False)
        self.assertFalse(report.ok)
        self.assertIn("seam_coverage_separated", [issue.code for issue in report.errors])

    def test_link_replacement_must_keep_endpoints(self):
        ledger = fx.base_ledger()
        with self.assertRaises(RegistrationError) as ctx:
            ledger.replace_link("LINK-B", ("LOCAL-A", 1), ("SITE", 2))
        self.assertEqual(ctx.exception.code, "invalid_link")

    def test_mixed_common_frames_cannot_be_joined(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        ledger.register_batch(fx.batch_w())
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        ledger.register_network(fx.site_network(x_shift=0.5, version=3))
        ledger.link_networks("LINK-A-3", ("LOCAL-A", 1), ("SITE", 3))
        ledger.link_networks("LINK-B-3", ("LOCAL-B", 1), ("SITE", 3))
        ledger.advance_common_network("SITE", 3)
        ledger.register_batch(fx.batch_w(batch_id="BATCH-B-3"))
        pw = ledger.build_partition("BATCH-B-3")
        self.assertEqual(pw.transform.target_version, 3)
        report = ledger.handover([pe.id, pw.id], require_delivered=False)
        codes = {issue.code for issue in report.errors}
        # The east snapshot is still in SITE@2 while the new west draft is in
        # SITE@3; they are not blended.
        self.assertIn("seam_frame_mismatch", codes)

    def test_uniform_promotion_keeps_old_pairings_valid(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        ledger.register_batch(fx.batch_w())
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        self.assertTrue(ledger.handover([pe.id, pw.id]).ok)
        ledger.register_network(fx.site_network(x_shift=0.5, version=3))
        ledger.link_networks("LINK-A-3", ("LOCAL-A", 1), ("SITE", 3))
        ledger.link_networks("LINK-B-3", ("LOCAL-B", 1), ("SITE", 3))
        ledger.advance_common_network("SITE", 3)
        # Both snapshots were cut in the same (now historical) frame; their
        # mutual seam remains valid and their base maps stay readable.
        report = ledger.handover([pe.id, pw.id])
        self.assertTrue(report.ok, report.to_dict())
        self.assertTrue(report.pair_checks[0].control_ok)

    def test_advance_common_network_requires_links_for_every_source(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        ledger.register_network(fx.site_network(version=3))
        ledger.register_network(
            ControlNetwork(
                id="LOCAL-A-3",
                crs="LOCAL-A",
                unit="mm",
                version=1,
                points={"CP-1": (0, 0), "CP-2": (10000, 0), "CP-3": (0, 10000)},
            )
        )
        ledger.link_networks("LINK-A-3", ("LOCAL-A-3", 1), ("SITE", 3))
        # BATCH-A-1 is still declared on LOCAL-A@1, which cannot reach SITE@3.
        with self.assertRaises(RegistrationError) as ctx:
            ledger.advance_common_network("SITE", 3)
        self.assertEqual(ctx.exception.code, "unlinked_common_network")


class PinnedPartitionTests(unittest.TestCase):
    def test_pinned_draft_cannot_be_refreshed(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        ledger.register_batch(fx.batch_w())
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        ledger.pin_design("D-22", [pe.id, pw.id], edition="E3")
        # Delivered already blocks; pinning blocks even a draft.
        draft = ledger.build_partition("BATCH-A-1", partition_id="P-DRAFT")
        ledger._references["D-23"] = ledger.design_reference("D-22").__class__(
            drawing="D-23",
            partition_ids=(draft.id,),
            partition_editions=((draft.id, draft.edition),),
            edition="E4",
        )
        with self.assertRaises(SeamError) as ctx:
            ledger.refresh_draft(draft.id)
        self.assertEqual(ctx.exception.code, "partition_locked")

    def test_designer_can_retrieve_historical_base_map(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        ledger.register_batch(fx.batch_w())
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        reference = ledger.pin_design("D-22", [pe.id, pw.id], edition="E3")
        # Deliver a replacement later; the pinned edition stays available.
        replacement = ledger.replace_partition(pe.id)
        ledger.deliver_partition(replacement.id)
        historical = ledger.partition(pe.id, reference.edition_of(pe.id))
        self.assertEqual(historical.coverage, pe.coverage)
        self.assertEqual(historical.status, "delivered")
        trace = ledger.trace_partition(pe.id, reference.edition_of(pe.id))
        self.assertEqual(trace["design_references"][0]["drawing"], "D-22")

    def test_duplicate_pin_is_refused(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        ledger.pin_design("D-22", [pe.id], edition="E1")
        with self.assertRaises(ConflictError) as ctx:
            ledger.pin_design("D-22", [pe.id], edition="E2")
        self.assertEqual(ctx.exception.code, "duplicate_design_reference")


if __name__ == "__main__":
    unittest.main()
