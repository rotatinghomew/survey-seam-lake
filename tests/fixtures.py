"""Shared fixtures for ledger tests.

Conventions
-----------
* The project common frame is ``SITE`` metres.
* TEAM-A works in ``LOCAL-A`` millimetres, TEAM-B in ``LOCAL-B`` millimetres.
* ``SITE`` v2 places LOCAL-A's origin at (1000, 1000) m and LOCAL-B's origin
  at (1010, 1000) m.  So ZONE-E spans x in [1000, 1010] and ZONE-W spans
  x in [1010, 1020]: adjacent, sharing the x = 1010 m seam.
"""

from __future__ import annotations

from seam_survey import (
    ControlNetwork,
    FeatureObservation,
    Project,
    ReviewRecord,
    SeamGroup,
    SurveyBatch,
    SurveyLedger,
    Zone,
)


ZONE_E_RESPONSIBILITY = [(1000.0, 1000.0), (1010.0, 1000.0), (1010.0, 1010.0), (1000.0, 1010.0)]
ZONE_W_RESPONSIBILITY = [(1010.0, 1000.0), (1020.0, 1000.0), (1020.0, 1010.0), (1010.0, 1010.0)]

# Delivered coverage in each team's own millimetre frame.
ZONE_E_COVERAGE_MM = [(0, 0), (10000, 0), (10000, 10000), (0, 10000)]
ZONE_W_COVERAGE_MM = [(0, 0), (10000, 0), (10000, 10000), (0, 10000)]


def site_network(x_shift: float = 0.0, *, version: int = 2) -> ControlNetwork:
    """SITE v2, optionally translated to emulate a later re-control."""
    return ControlNetwork(
        id="SITE",
        crs="SITE",
        unit="m",
        version=version,
        points={
            "CP-1": (1000.0 + x_shift, 1000.0),
            "CP-2": (1010.0 + x_shift, 1000.0),
            "CP-3": (1000.0 + x_shift, 1010.0),
            "CP-4": (1010.0 + x_shift, 1000.0),
            "CP-5": (1020.0 + x_shift, 1000.0),
            "CP-6": (1010.0 + x_shift, 1010.0),
        },
    )


def local_a() -> ControlNetwork:
    return ControlNetwork(
        id="LOCAL-A",
        crs="LOCAL-A",
        unit="mm",
        version=1,
        points={"CP-1": (0, 0), "CP-2": (10000, 0), "CP-3": (0, 10000)},
    )


def local_b() -> ControlNetwork:
    return ControlNetwork(
        id="LOCAL-B",
        crs="LOCAL-B",
        unit="mm",
        version=1,
        points={"CP-4": (0, 0), "CP-5": (10000, 0), "CP-6": (0, 10000)},
    )


def project() -> Project:
    return Project(
        id="PROJ-LAKE",
        name="古建测绘接茬",
        common_network_id="SITE",
        common_network_version=2,
    )


def zone_e() -> Zone:
    return Zone(
        id="ZONE-E",
        owner="TEAM-A",
        responsibility=ZONE_E_RESPONSIBILITY,
        target_area_m2=100.0,
    )


def zone_w() -> Zone:
    return Zone(
        id="ZONE-W",
        owner="TEAM-B",
        responsibility=ZONE_W_RESPONSIBILITY,
        target_area_m2=100.0,
    )


def zone_east_extension() -> Zone:
    return Zone(
        id="ZONE-W2",
        owner="TEAM-B",
        responsibility=[(1020.0, 1000.0), (1030.0, 1000.0), (1030.0, 1010.0), (1020.0, 1010.0)],
        target_area_m2=100.0,
    )


ZONE_W2_COVERAGE_MM = [(10000, 0), (20000, 0), (20000, 10000), (10000, 10000)]


def batch_e(
    *,
    batch_id: str = "BATCH-A-1",
    network_id: str = "LOCAL-A",
    network_version: int = 1,
    coverage=ZONE_E_COVERAGE_MM,
    observations=(),
) -> SurveyBatch:
    return SurveyBatch(
        id=batch_id,
        zone_id="ZONE-E",
        network_id=network_id,
        network_version=network_version,
        coverage=tuple(coverage),
        observations=tuple(observations),
    )


def batch_w(
    *,
    batch_id: str = "BATCH-B-1",
    network_id: str = "LOCAL-B",
    network_version: int = 1,
    coverage=ZONE_W_COVERAGE_MM,
    observations=(),
) -> SurveyBatch:
    return SurveyBatch(
        id=batch_id,
        zone_id="ZONE-W",
        network_id=network_id,
        network_version=network_version,
        coverage=tuple(coverage),
        observations=tuple(observations),
    )


def observation(
    observation_id: str,
    *,
    zone_id: str,
    batch_id: str,
    shape,
    review: str = "pending",
    source: str = "",
) -> FeatureObservation:
    return FeatureObservation(
        id=observation_id,
        zone_id=zone_id,
        batch_id=batch_id,
        shape=tuple(shape),
        source=source,
        review=review,
    )


def base_ledger(*, link_both: bool = True) -> SurveyLedger:
    ledger = SurveyLedger(project())
    ledger.register_network(local_a())
    ledger.register_network(local_b())
    ledger.register_network(site_network())
    if link_both:
        ledger.link_networks("LINK-A", ("LOCAL-A", 1), ("SITE", 2))
        ledger.link_networks("LINK-B", ("LOCAL-B", 1), ("SITE", 2))
    ledger.register_zone(zone_e())
    ledger.register_zone(zone_w())
    return ledger


def seam_same_object(observation_ids=("F-A-01", "F-B-09")) -> SeamGroup:
    return SeamGroup(
        id="SEAM-1",
        zone_a="ZONE-E",
        zone_b="ZONE-W",
        observation_ids=tuple(observation_ids),
    )


def review_same_object(
    *,
    review_id: str = "REV-1",
    primary: str = "F-A-01",
    comparisons=("F-B-09",),
    seam_id: str | None = "SEAM-1",
    control_basis: str | None = "LINK-A",
) -> ReviewRecord:
    return ReviewRecord(
        id=review_id,
        subject=seam_id or primary,
        decision="same_object",
        reviewer="验收员",
        primary_id=primary,
        comparison_ids=tuple(comparisons),
        seam_id=seam_id,
        control_basis=control_basis,
    )
