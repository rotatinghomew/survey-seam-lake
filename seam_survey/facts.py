"""Load the local synthetic fact file into a ledger.

The sketch in ``data/facts.json`` only carries control networks, adjacent zones
and two observations that straddle the seam.  It has no batches, no links and
no acceptance records, so :func:`load_facts` registers what it can and reports
what is still missing instead of inventing the rest.

Zone responsibility areas are registered in the project common frame.  Where a
zone names the network its coverage was scanned in, the fixture's point list is
read in that network's unit and projected through the registered link chain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .errors import RegistrationError
from .models import (
    ControlNetwork,
    FeatureObservation,
    Project,
    SurveyBatch,
    Zone,
)
from .ledger import SurveyLedger


def load_facts(path: str | Path) -> tuple[SurveyLedger, list[str]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    gaps: list[str] = []

    networks = [ControlNetwork.from_dict(row) for row in payload.get("control_networks", [])]
    if not networks:
        raise RegistrationError("facts file registers no control networks", code="empty_fixture")
    common_name = payload.get("common_network")
    if common_name is None:
        site_like = [network for network in networks if network.crs == "SITE"]
        common = max(site_like or networks, key=lambda network: network.version)
    else:
        candidates = [network for network in networks if network.id == common_name]
        if not candidates:
            raise RegistrationError(
                f"facts file names common network {common_name!r} but does not register it",
                code="missing_common_network",
            )
        common = max(candidates, key=lambda network: network.version)

    project_info = payload.get("project", {})
    project = Project(
        id=project_info.get("id", "PROJECT"),
        name=project_info.get("name", ""),
        common_network_id=common.id,
        common_network_version=common.version,
    )
    ledger = SurveyLedger(project)
    for network in networks:
        ledger.register_network(network)

    for network in networks:
        key = (network.id, network.version)
        if key == (common.id, common.version):
            continue
        shared = network.point_codes & common.point_codes
        if len(shared) < ledger.link_builder.min_common_points:
            gaps.append(
                f"{network.id}@{network.version} shares {len(shared)} registered points with "
                f"{common.id}@{common.version}; no link registered"
            )
            continue
        ledger.link_networks(f"LINK-{network.id}-{network.version}", key, (common.id, common.version))

    def select_network(row: dict) -> ControlNetwork | None:
        network_id = row.get("network")
        requested = row.get("network_version")
        matches = [
            network
            for network in networks
            if network.id == network_id
            and (requested is None or network.version == int(requested))
        ]
        return max(matches, key=lambda network: network.version) if matches else None

    zone_rows = payload.get("zones", [])
    for row in zone_rows:
        raw_points = row.get("points") or row.get("responsibility") or []
        network = select_network(row)
        if row.get("network") is None:
            points: Sequence = tuple(tuple(point) for point in raw_points)
        elif network is None:
            gaps.append(f"zone {row['id']} names unknown network {row.get('network')!r}")
            continue
        else:
            try:
                points = ledger.to_common(
                    network.id, network.version, tuple(tuple(point) for point in raw_points)
                )
            except RegistrationError as exc:
                gaps.append(f"zone {row['id']} cannot be projected to the common frame: {exc}")
                continue
        ledger.register_zone(
            Zone(
                id=row["id"],
                owner=row.get("owner", ""),
                responsibility=tuple(points),
                target_area_m2=row.get("coverage_target_m2", row.get("target_area_m2")),
                start_m=row.get("start_m"),
                end_m=row.get("end_m"),
            )
        )

    observations_by_zone: dict[str, list[FeatureObservation]] = {}
    for row in payload.get("feature_observations", []):
        observation = FeatureObservation.from_dict(row)
        observations_by_zone.setdefault(observation.zone_id, []).append(observation)

    for row in zone_rows:
        zone_id = row["id"]
        network = select_network(row)
        if row.get("network") is None:
            gaps.append(f"zone {zone_id} names no scanned network; no batch registered")
            continue
        if network is None:
            gaps.append(f"zone {zone_id} names unknown network {row.get('network')!r}")
            continue
        batch_id = row.get("batch") or f"BATCH-{zone_id}"
        observations = tuple(
            FeatureObservation(
                id=item.id,
                zone_id=item.zone_id,
                batch_id=batch_id,
                shape=item.shape,
                source=item.source,
                review=item.review,
                note=item.note,
            )
            for item in observations_by_zone.get(zone_id, [])
        )
        coverage = row.get("points") or row.get("responsibility")
        ledger.register_batch(
            SurveyBatch(
                id=batch_id,
                zone_id=zone_id,
                network_id=network.id,
                network_version=network.version,
                coverage=tuple(tuple(point) for point in coverage),
                observations=observations,
            )
        )

    for reference in payload.get("design_references", []):
        gaps.append(
            f"drawing {reference.get('drawing')} references zones "
            f"{reference.get('zones')}; the sketch has no partition ids to resolve"
        )
    for issue in ledger.zone_frame_issues():
        gaps.append(issue.message)
    if not ledger._seams:
        gaps.append("the sketch registers no seam group or acceptance record")

    return ledger, gaps
