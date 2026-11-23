import unittest

from seam_survey import ControlNetwork, SeamError, SurveyBatch

import fixtures as fx


class RecontrolWorkflowTests(unittest.TestCase):
    """A later control result changes local coordinates mid-project."""

    def _ledger_with_delivered_east(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        delivered = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        return ledger, delivered

    def test_undelivered_draft_is_refreshed_after_control_update(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        draft = ledger.build_partition("BATCH-A-1")
        self.assertEqual(draft.edition, 1)

        # SITE v3 moves TEAM-A's stations 0.5 m east.
        ledger.register_network(fx.site_network(x_shift=0.5, version=3))
        ledger.link_networks("LINK-A-3", ("LOCAL-A", 1), ("SITE", 3))
        ledger.advance_common_network("SITE", 3)

        refreshed = ledger.refresh_draft(draft.id)
        self.assertEqual(refreshed.edition, 2)
        self.assertEqual(refreshed.transform.target_version, 3)
        # The measured geometry moved with the new control; it is reported as
        # off its old responsibility area, not stretched back onto it.
        self.assertFalse(refreshed.coverage_ok)
        self.assertAlmostEqual(refreshed.coverage[0].x, 1000.5, places=6)
        with self.assertRaises(SeamError):
            ledger.deliver_partition(refreshed.id)

    def test_delivered_partition_survives_recontrol_untouched(self):
        ledger, delivered = self._ledger_with_delivered_east()
        original = delivered.coverage

        ledger.register_network(fx.site_network(x_shift=0.5, version=3))
        ledger.link_networks("LINK-A-3", ("LOCAL-A", 1), ("SITE", 3))
        ledger.advance_common_network("SITE", 3)

        self.assertEqual(ledger.partition(delivered.id).coverage, original)
        self.assertEqual(ledger.partition(delivered.id).status, "delivered")
        # A new snapshot can be cut on the new basis without rewriting history.
        replacement = ledger.replace_partition(delivered.id)
        self.assertEqual(replacement.replaces, delivered.id)
        self.assertNotEqual(replacement.coverage, original)

    def test_reassociate_then_deliver_on_new_basis(self):
        ledger = fx.base_ledger()
        ledger.register_network(fx.site_network(x_shift=0.5, version=3))
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
        ledger.advance_common_network("SITE", 3)
        ledger.register_batch(
            SurveyBatch(
                id="BATCH-A-3",
                zone_id="ZONE-E",
                network_id="LOCAL-A-3",
                network_version=1,
                coverage=tuple(fx.ZONE_E_COVERAGE_MM),
            )
        )
        partition = ledger.build_partition("BATCH-A-3")
        # The new control result moved the scan 0.5 m east of the registered
        # responsibility area; that is reported, not absorbed.
        self.assertEqual(partition.transform.target_version, 3)
        self.assertAlmostEqual(partition.coverage[0].x, 1000.5, places=6)
        self.assertFalse(partition.coverage_ok)
        self.assertAlmostEqual(partition.gap_area_m2, 5.0, places=6)

    def test_cannot_deliver_after_control_update_without_redelivery(self):
        ledger, delivered = self._ledger_with_delivered_east()
        ledger.register_batch(fx.batch_w())
        ledger.register_network(fx.site_network(x_shift=0.5, version=3))
        ledger.link_networks("LINK-A-3", ("LOCAL-A", 1), ("SITE", 3))
        ledger.link_networks("LINK-B-3", ("LOCAL-B", 1), ("SITE", 3))
        # Promoting the frame moves TEAM-B's delivered batch 0.5 m relative to
        # its registered responsibility.  A new draft must therefore fail the
        # coverage check rather than be stretched to fit.
        ledger.advance_common_network("SITE", 3)
        self.assertEqual(ledger.network("SITE", 2).status, "superseded")
        draft = ledger.build_partition("BATCH-B-1")
        readiness = ledger.partition_readiness(draft.id)
        self.assertFalse(readiness.ready)
        self.assertIn("coverage_gap", [issue.code for issue in readiness.errors])
        self.assertFalse(draft.coverage_ok)
        self.assertGreater(draft.gap_area_m2, 4.0)
        # The already-delivered east partition is unaffected and still readable.
        self.assertEqual(ledger.partition(delivered.id).status, "delivered")
        self.assertEqual(ledger.partition(delivered.id).coverage, delivered.coverage)
        self.assertEqual(ledger.partition(delivered.id).coverage, delivered.coverage)


if __name__ == "__main__":
    unittest.main()
