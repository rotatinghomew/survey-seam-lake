import json
import unittest

from seam_survey import (
    BatchDiagnostics,
    ControlNetwork,
    DesignReference,
    FeatureObservation,
    HandoverReport,
    Issue,
    PairCheck,
    Partition,
    PartitionFeature,
    PartitionReadiness,
    Project,
    ReviewRecord,
    SeamAssessment,
    SeamGroup,
    SurveyBatch,
    TransformRecord,
    Zone,
)

import fixtures as fx


class SerialisationTests(unittest.TestCase):
    def test_all_dto_payloads_are_json_safe(self):
        ledger = fx.base_ledger()
        ledger.register_batch(
            fx.batch_e(
                observations=[
                    fx.observation(
                        "F-A-01",
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
                        "F-B-09",
                        zone_id="ZONE-W",
                        batch_id="BATCH-B-1",
                        shape=[(-200, 4000), (300, 4000)],
                    )
                ]
            )
        )
        ledger.register_seam(fx.seam_same_object())
        ledger.record_review(fx.review_same_object())
        pe = ledger.deliver_partition(ledger.build_partition("BATCH-A-1").id)
        pw = ledger.deliver_partition(ledger.build_partition("BATCH-B-1").id)
        ledger.pin_design("D-22", [pe.id, pw.id], edition="E3")
        report = ledger.handover([pe.id, pw.id])
        payload = {
            "dump": ledger.dump(),
            "handover": report.to_dict(),
            "trace": ledger.trace_partition(pe.id),
            "readiness": ledger.partition_readiness(pe.id).to_dict(),
            "seam": ledger.assess_seam("SEAM-1").to_dict(),
            "lineage": ledger.link_lineage(("LOCAL-A", 1), ("LOCAL-B", 1)),
            "plan": ledger.replacement_plan([pe.id, pw.id]),
        }
        text = json.dumps(payload, ensure_ascii=False)
        self.assertIn("P-BATCH-A-1", text)
        decoded = json.loads(text)
        self.assertEqual(decoded["handover"]["ok"], True)

    def test_partition_round_trips_through_dict(self):
        ledger = fx.base_ledger()
        ledger.register_batch(fx.batch_e())
        original = ledger.build_partition("BATCH-A-1")
        restored = Partition.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored.id, original.id)
        self.assertEqual(restored.coverage, original.coverage)
        self.assertEqual(restored.transform.scale, original.transform.scale)
        self.assertEqual(restored.gap_area_m2, original.gap_area_m2)

    def test_batch_round_trip(self):
        batch = fx.batch_e(
            observations=[
                fx.observation(
                    "F-A-01", zone_id="ZONE-E", batch_id="BATCH-A-1", shape=[(0, 0), (1, 0)]
                )
            ]
        )
        restored = SurveyBatch.from_dict(json.loads(json.dumps(batch.to_dict())))
        self.assertEqual(restored.id, batch.id)
        self.assertEqual(restored.coverage, batch.coverage)
        self.assertEqual(restored.observations[0].shape, batch.observations[0].shape)

    def test_control_network_round_trip(self):
        network = fx.site_network()
        restored = ControlNetwork.from_dict(json.loads(json.dumps(network.to_dict())))
        self.assertEqual(restored.points, network.points)
        self.assertEqual(restored.unit, "m")

    def test_zone_round_trip(self):
        zone = fx.zone_e()
        restored = Zone.from_dict(json.loads(json.dumps(zone.to_dict())))
        self.assertEqual(restored.responsibility, zone.responsibility)
        self.assertAlmostEqual(restored.area_m2, zone.area_m2)

    def test_comparison_feature_cannot_be_counted(self):
        with self.assertRaises(Exception):
            PartitionFeature(
                observation_id="X", zone_id="Z", shape=(), role="comparison", counted=True
            )

    def test_pending_feature_cannot_be_counted(self):
        with self.assertRaises(Exception):
            PartitionFeature(
                observation_id="X", zone_id="Z", shape=(), role="pending", counted=True
            )


if __name__ == "__main__":
    unittest.main()
