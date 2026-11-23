"""The project ledger: registration, diagnostics, partitions and handover.

Design invariants
-----------------
* Raw measurements are stored untouched, in the unit and network version the
  team delivered.
* Every movement between frames uses a registered :class:`ControlLink` built
  from same-named registered control points of explicit versions.
* A :class:`Partition` is an immutable snapshot.  Delivered/pinned partitions
  are never rewritten in place; replacements are new partitions with lineage.
* Coverage is always assessed in the project common frame against the
  pre-registered responsibility area.
* A seam without an acceptance decision cannot contribute duplicated geometry
  to fill a coverage gap.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from .errors import (
    ConflictError,
    CoverageError,
    LedgerError,
    RegistrationError,
    SeamError,
)
from .geometry import (
    Vec2,
    convex_intersection,
    intersection_area_with_union,
    polygon_area,
    polygon_min_distance,
    polygon_union_area,
    shape_overlap,
)
from .lineage import LinkGraph, LinkPath, compose, invert_similarity
from .models import (
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
from .transform import ControlLink, LinkBuilder, Similarity


class SurveyLedger:
    def __init__(
        self,
        project: Project,
        *,
        min_common_points: int = 3,
        residual_tolerance_m: float = 0.02,
        max_scale_deviation: float = 0.05,
        coverage_tolerance_m2: float = 0.01,
        seam_tolerance_m: float = 0.05,
        feature_tolerance_m: float = 0.05,
        adjacency_tolerance_m: float = 0.05,
    ) -> None:
        self.project = project
        if (
            coverage_tolerance_m2 < 0
            or seam_tolerance_m < 0
            or feature_tolerance_m < 0
            or adjacency_tolerance_m < 0
        ):
            raise RegistrationError("tolerances cannot be negative", code="invalid_settings")
        self.coverage_tolerance_m2 = coverage_tolerance_m2
        self.seam_tolerance_m = seam_tolerance_m
        self.feature_tolerance_m = feature_tolerance_m
        self.adjacency_tolerance_m = adjacency_tolerance_m
        self.link_builder = LinkBuilder(
            min_common_points=min_common_points,
            residual_tolerance_m=residual_tolerance_m,
            max_scale_deviation=max_scale_deviation,
        )
        self._networks: dict[tuple[str, int], ControlNetwork] = {}
        self._zones: dict[str, Zone] = {}
        self._batches: dict[str, SurveyBatch] = {}
        self._observations: dict[str, FeatureObservation] = {}
        self._seams: dict[str, SeamGroup] = {}
        self._reviews: dict[str, ReviewRecord] = {}
        self._links = LinkGraph()
        self._partitions: dict[str, list[Partition]] = {}
        self._references: dict[str, DesignReference] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_network(self, network: ControlNetwork) -> ControlNetwork:
        key = (network.id, network.version)
        if key in self._networks:
            raise ConflictError(
                f"network {network.id}@{network.version} is already registered",
                code="duplicate_network",
            )
        same_id = [version for network_id, version in self._networks if network_id == network.id]
        if same_id and network.version <= max(same_id):
            raise RegistrationError(
                f"version {network.version} of network {network.id} does not supersede "
                f"registered version {max(same_id)}",
                code="version_out_of_order",
            )
        # Registering a newer version makes it the applicable control for new
        # batches.  Older versions remain active for batches already tied to
        # them; they are marked superseded only when the project explicitly
        # promotes the frame (see `advance_common_network`).
        self._networks[key] = network
        return network

    def supersede_network(self, network_id: str, version: int, *, note: str = "") -> ControlNetwork:
        """Withdraw a control version from use without deleting its history."""
        network = self._require_network(network_id, version)
        note = note or f"{network_id}@{version} withdrawn"
        self._set_status(network_id, version, "superseded", note)
        return self._networks[(network_id, version)]

    def _set_status(self, network_id: str, version: int, status: str, note: str = "") -> None:
        network = self._networks.get((network_id, version))
        if network is None:
            return
        self._networks[(network_id, version)] = replace(
            network, status=status, note=note or network.note
        )

    def applicable_version(self, network_id: str) -> int | None:
        versions = [version for other_id, version in self._networks if other_id == network_id]
        return max(versions) if versions else None

    def register_zone(self, zone: Zone) -> Zone:
        if zone.id in self._zones:
            raise ConflictError(f"zone {zone.id!r} is already registered", code="duplicate_zone")
        self._zones[zone.id] = zone
        return zone

    def register_batch(self, batch: SurveyBatch) -> SurveyBatch:
        if batch.id in self._batches:
            raise ConflictError(f"batch {batch.id!r} is already registered", code="duplicate_batch")
        if batch.zone_id not in self._zones:
            raise RegistrationError(
                f"batch {batch.id} refers to unregistered zone {batch.zone_id!r}",
                code="unknown_zone",
            )
        self._require_network(batch.network_id, batch.network_version)
        for observation in batch.observations:
            self._check_observation_ids(observation)
        self._batches[batch.id] = batch
        for observation in batch.observations:
            self._observations[observation.id] = observation
        return batch

    def register_observation(self, observation: FeatureObservation) -> FeatureObservation:
        self._check_observation_ids(observation)
        try:
            batch = self._batches[observation.batch_id]
        except KeyError as exc:
            raise RegistrationError(
                f"observation {observation.id} refers to unregistered batch {observation.batch_id!r}",
                code="unknown_batch",
            ) from exc
        if observation.zone_id != batch.zone_id:
            raise RegistrationError(
                f"observation {observation.id} zone {observation.zone_id} does not match "
                f"batch zone {batch.zone_id}",
                code="observation_zone_mismatch",
            )
        updated = replace(batch, observations=batch.observations + (observation,))
        self._batches[batch.id] = updated
        self._observations[observation.id] = observation
        return observation

    def _check_observation_ids(self, observation: FeatureObservation) -> None:
        if observation.id in self._observations:
            raise ConflictError(
                f"observation {observation.id!r} is already registered", code="duplicate_observation"
            )

    def reassign_batch_network(
        self,
        batch_id: str,
        network_id: str,
        version: int,
        *,
        note: str = "",
    ) -> SurveyBatch:
        """Move an existing batch onto a newer registered network version.

        Used when a re-issued control result is the applicable basis: the raw
        measurements are unchanged, only their declared control association
        moves.  The target version must be registered and linked, and all
        observations keep their original coordinates and unit.
        """
        batch = self.batch(batch_id)
        self._require_network(network_id, version)
        if (batch.network_id, batch.network_version) == (network_id, version):
            return batch
        # Repository-wide: the batch must be able to reach the common frame on
        # the new basis, and the change must not be a silent unit switch.
        self._links.find_path((network_id, version), (
            self.project.common_network_id,
            self.project.common_network_version,
        ))
        updated = replace(
            batch,
            network_id=network_id,
            network_version=version,
            note=note or batch.note,
        )
        self._batches[batch_id] = updated
        return updated

    def register_seam(self, seam: SeamGroup) -> SeamGroup:
        if seam.id in self._seams:
            raise ConflictError(f"seam {seam.id!r} is already registered", code="duplicate_seam")
        if seam.zone_a not in self._zones or seam.zone_b not in self._zones:
            raise RegistrationError(
                f"seam {seam.id} refers to unregistered zone", code="unknown_zone"
            )
        if not seam.observation_ids:
            raise RegistrationError(
                f"seam {seam.id} lists no observations", code="invalid_seam"
            )
        zones_seen: set[str] = set()
        for observation_id in seam.observation_ids:
            observation = self._observations.get(observation_id)
            if observation is None:
                raise RegistrationError(
                    f"seam {seam.id} refers to unknown observation {observation_id!r}",
                    code="unknown_observation",
                )
            if observation.zone_id not in {seam.zone_a, seam.zone_b}:
                raise RegistrationError(
                    f"observation {observation_id} is outside seam zones", code="invalid_seam"
                )
            zones_seen.add(observation.zone_id)
        if zones_seen != {seam.zone_a, seam.zone_b}:
            raise RegistrationError(
                f"seam {seam.id} must carry at least one measurement from each adjacent zone",
                code="seam_missing_side",
                details={"zones_present": sorted(zones_seen)},
            )
        self._seams[seam.id] = seam
        return seam

    def link_networks(
        self,
        link_id: str,
        source: tuple[str, int],
        target: tuple[str, int],
        *,
        note: str = "",
        registered_at: str | None = None,
    ) -> ControlLink:
        return self._register_link(
            link_id, source, target, note=note, registered_at=registered_at
        )

    def replace_link(
        self,
        link_id: str,
        source: tuple[str, int],
        target: tuple[str, int],
        *,
        note: str = "",
        registered_at: str | None = None,
    ) -> ControlLink:
        """Register a re-issued control result for an existing link.

        Snapshots that already cite the old link keep their frozen geometry,
        so downstream handovers can detect that their basis has moved instead
        of being quietly re-projected.
        """
        old = self._links.get(link_id)
        if (old.source_id, old.source_version) != source or (
            old.target_id,
            old.target_version,
        ) != target:
            raise RegistrationError(
                f"link {link_id} joins {old.source_id}@{old.source_version} to "
                f"{old.target_id}@{old.target_version}; a replacement must keep the endpoints",
                code="invalid_link",
            )
        new_link = self._build_link(
            link_id, source, target, note=note, registered_at=registered_at
        )
        self._links.replace(link_id, new_link)
        return new_link

    def _register_link(
        self,
        link_id: str,
        source: tuple[str, int],
        target: tuple[str, int],
        *,
        note: str,
        registered_at: str | None,
    ) -> ControlLink:
        link = self._build_link(
            link_id, source, target, note=note, registered_at=registered_at
        )
        self._links.add(link)
        return link

    def _build_link(
        self,
        link_id: str,
        source: tuple[str, int],
        target: tuple[str, int],
        *,
        note: str,
        registered_at: str | None,
    ) -> ControlLink:
        source_network = self._require_network(*source)
        target_network = self._require_network(*target)
        if source == target:
            raise RegistrationError(
                "a control link must join two different network versions", code="invalid_link"
            )
        assessment = self.link_builder.assess(source_network, target_network)
        return ControlLink(
            id=link_id,
            source_id=source[0],
            source_version=source[1],
            target_id=target[0],
            target_version=target[1],
            assessment=assessment,
            min_common_points=self.link_builder.min_common_points,
            residual_tolerance_m=self.link_builder.residual_tolerance_m,
            max_scale_deviation=self.link_builder.max_scale_deviation,
            note=note,
            registered_at=registered_at,
        )

    def _require_network(self, network_id: str, version: int) -> ControlNetwork:
        try:
            return self._networks[(network_id, version)]
        except KeyError as exc:
            raise RegistrationError(
                f"network {network_id}@{version} is not registered", code="unknown_network"
            ) from exc

    def _common_network(self) -> ControlNetwork:
        network = self._require_network(
            self.project.common_network_id, self.project.common_network_version
        )
        return network

    def advance_common_network(
        self, network_id: str, version: int
    ) -> Project:
        """Promote a registered network version to the project common frame.

        Existing partitions keep their frozen coordinates; new partitions are
        projected through the registered links to the new common version.  A
        handover that mixes the two will surface the disagreement in
        :meth:`handover` rather than silently blending frames.
        """
        network = self._require_network(network_id, version)
        if (network_id, version) == (
            self.project.common_network_id,
            self.project.common_network_version,
        ):
            return self.project
        # Every source version actually used by a registered batch must reach
        # the new common frame through registered links; otherwise the
        # promotion would strand those batches.
        used_sources = {
            (batch.network_id, batch.network_version) for batch in self._batches.values()
        }
        stranded: list[str] = []
        for source in sorted(used_sources):
            if source == (network_id, version):
                continue
            try:
                self._links.find_path(source, (network_id, version))
            except RegistrationError:
                stranded.append(f"{source[0]}@{source[1]}")
        if stranded:
            raise RegistrationError(
                f"cannot promote {network_id}@{version}: no registered link path from "
                f"{', '.join(stranded)}",
                code="unlinked_common_network",
                details={"stranded": stranded},
            )
        previous = (
            self.project.common_network_id,
            self.project.common_network_version,
        )
        self.project = replace(
            self.project,
            common_network_id=network_id,
            common_network_version=version,
        )
        if previous[0] == network_id and previous[1] < version:
            # The project has formally moved on: the old common frame is no
            # longer the applicable basis for new work.
            self.supersede_network(
                previous[0],
                previous[1],
                note=f"common frame promoted to {network_id}@{version}",
            )
        return self.project

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    def network(self, network_id: str, version: int) -> ControlNetwork:
        return self._require_network(network_id, version)

    def zone(self, zone_id: str) -> Zone:
        try:
            return self._zones[zone_id]
        except KeyError as exc:
            raise RegistrationError(f"unknown zone {zone_id!r}", code="unknown_zone") from exc

    def batch(self, batch_id: str) -> SurveyBatch:
        try:
            return self._batches[batch_id]
        except KeyError as exc:
            raise RegistrationError(f"unknown batch {batch_id!r}", code="unknown_batch") from exc

    def observation(self, observation_id: str) -> FeatureObservation:
        try:
            return self._observations[observation_id]
        except KeyError as exc:
            raise RegistrationError(
                f"unknown observation {observation_id!r}", code="unknown_observation"
            ) from exc

    def seam(self, seam_id: str) -> SeamGroup:
        try:
            return self._seams[seam_id]
        except KeyError as exc:
            raise RegistrationError(f"unknown seam {seam_id!r}", code="unknown_seam") from exc

    def partition(self, partition_id: str, edition: int | None = None) -> Partition:
        try:
            editions = self._partitions[partition_id]
        except KeyError as exc:
            raise RegistrationError(f"unknown partition {partition_id!r}", code="unknown_partition") from exc
        if edition is None:
            return editions[-1]
        for item in editions:
            if item.edition == edition:
                return item
        raise RegistrationError(
            f"partition {partition_id!r} has no edition {edition}", code="unknown_edition"
        )

    def partition_history(self, partition_id: str) -> tuple[Partition, ...]:
        try:
            return tuple(self._partitions[partition_id])
        except KeyError as exc:
            raise RegistrationError(f"unknown partition {partition_id!r}", code="unknown_partition") from exc

    def link(self, link_id: str) -> ControlLink:
        return self._links.get(link_id)

    # ------------------------------------------------------------------
    # Coordinate lineage
    # ------------------------------------------------------------------
    def _path_for_batch(self, batch: SurveyBatch) -> LinkPath:
        source = (batch.network_id, batch.network_version)
        target = (self.project.common_network_id, self.project.common_network_version)
        return self._links.find_path(source, target)

    def transform_record(self, batch_id: str) -> TransformRecord:
        batch = self.batch(batch_id)
        path = self._path_for_batch(batch)
        network = self._require_network(batch.network_id, batch.network_version)
        common = self._common_network()
        record = path.record(
            source_unit=network.unit, target_unit=common.unit, target_crs=common.crs
        )
        record = replace(
            record, link_ids=tuple(step.link.id for step in path.steps)
        )
        return record

    def _to_common(self, batch: SurveyBatch, shape: Sequence[Vec2]) -> tuple[Vec2, ...]:
        path = self._path_for_batch(batch)
        factor = self._require_network(batch.network_id, batch.network_version).metre_factor
        return tuple(path.transform.apply(point * factor) for point in shape)

    def to_common(
        self, network_id: str, version: int, points: Sequence[Sequence[float]]
    ) -> tuple[Vec2, ...]:
        """Project raw points of a registered network into the common frame.

        Used for pre-registering zone responsibility areas; it follows exactly
        the same registered link chain as batch projections.
        """
        network = self._require_network(network_id, version)
        path = self._links.find_path(
            (network_id, version),
            (self.project.common_network_id, self.project.common_network_version),
        )
        factor = network.metre_factor
        from .geometry import parse_point

        return tuple(
            path.transform.apply(parse_point(point) * factor) for point in points
        )

    # ------------------------------------------------------------------
    # Registered-frame integrity
    # ------------------------------------------------------------------
    def zone_frame_issues(self) -> tuple[Issue, ...]:
        """Check that registered responsibility areas tile without seams.

        Adjacent responsibility areas may share a boundary, but a gap or an
        overlap between them means the pre-registered zones themselves disagree
        about the common frame (a typical outcome of a later control result).
        """
        issues: list[Issue] = []
        zones = [self._zones[key] for key in sorted(self._zones)]
        for index, first in enumerate(zones):
            for second in zones[index + 1 :]:
                distance = polygon_min_distance(first.responsibility, second.responsibility)
                overlap = intersection_area_with_union(
                    first.responsibility, [second.responsibility]
                )
                if distance > self.adjacency_tolerance_m:
                    issues.append(
                        Issue(
                            "zone_responsibility_gap",
                            f"registered zones {first.id} and {second.id} are {distance:.4f} m "
                            "apart; the adjoining boundary is not shared",
                            severity="warning",
                            details={
                                "zones": [first.id, second.id],
                                "distance_m": distance,
                            },
                        )
                    )
                if overlap > self.coverage_tolerance_m2:
                    issues.append(
                        Issue(
                            "zone_responsibility_overlap",
                            f"registered zones {first.id} and {second.id} overlap by "
                            f"{overlap:.4f} m2; responsibility areas should tile",
                            severity="warning",
                            details={
                                "zones": [first.id, second.id],
                                "overlap_area_m2": overlap,
                            },
                        )
                    )
        return tuple(issues)

    # ------------------------------------------------------------------
    # Batch diagnostics
    # ------------------------------------------------------------------
    def diagnose_batch(self, batch_id: str) -> BatchDiagnostics:
        batch = self.batch(batch_id)
        issues: list[Issue] = []
        coverage: tuple[Vec2, ...] | None = None
        responsibility: tuple[Vec2, ...] | None = None
        transform: TransformRecord | None = None
        gap = overhang = 0.0
        coverage_area = 0.0
        covered = 0.0
        coverage_ok = False

        try:
            zone = self.zone(batch.zone_id)
        except RegistrationError as exc:
            issues.append(Issue("unknown_zone", str(exc)))
            zone = None
        try:
            network = self._require_network(batch.network_id, batch.network_version)
        except RegistrationError as exc:
            issues.append(Issue("unknown_network", str(exc)))
            network = None
        if network is not None and network.status != "active":
            issues.append(
                Issue(
                    "superseded_network",
                    f"network {network.id}@{network.version} is {network.status}; this batch "
                    "predates the applicable control. Existing snapshots stay valid, but a new "
                    "partition should be redelivered or reassociated.",
                    severity="warning",
                    details={"network": network.id, "version": network.version},
                )
            )
        if zone is not None and network is not None:
            try:
                transform = self.transform_record(batch_id)
                coverage = self._to_common(batch, batch.coverage)
                responsibility = zone.responsibility
                coverage_area = polygon_area(coverage)
                covered = polygon_area(convex_intersection([responsibility, coverage]))
                gap = max(0.0, zone.area_m2 - covered)
                overhang = max(0.0, coverage_area - covered)
                coverage_ok = gap <= self.coverage_tolerance_m2
                if not coverage_ok:
                    issues.append(
                        Issue(
                            "coverage_gap",
                            f"batch {batch.id} leaves {gap:.4f} m2 of zone {zone.id} uncovered "
                            f"(tolerance {self.coverage_tolerance_m2:.4f} m2)",
                            details={
                                "zone": zone.id,
                                "gap_area_m2": gap,
                                "covered_area_m2": covered,
                                "zone_area_m2": zone.area_m2,
                            },
                        )
                    )
                if overhang > self.coverage_tolerance_m2:
                    issues.append(
                        Issue(
                            "coverage_overhang",
                            f"batch {batch.id} covers {overhang:.4f} m2 outside zone {zone.id}",
                            severity="warning",
                            details={"zone": zone.id, "overhang_area_m2": overhang},
                        )
                    )
                if zone.target_area_m2 is not None and covered + self.coverage_tolerance_m2 < zone.target_area_m2:
                    issues.append(
                        Issue(
                            "target_area_not_met",
                            f"batch {batch.id} covers {covered:.4f} m2 of a "
                            f"{zone.target_area_m2:.4f} m2 target",
                            severity="warning",
                            details={"target_area_m2": zone.target_area_m2, "covered_area_m2": covered},
                        )
                    )
                issues.extend(self._observation_location_issues(batch, coverage))
                issues.extend(self._control_conflict_issues(batch))
            except LedgerError as exc:
                issues.append(Issue(exc.code, str(exc), details=dict(exc.details)))

        return BatchDiagnostics(
            batch_id=batch.id,
            issues=tuple(issues),
            transform=transform,
            coverage=coverage,
            responsibility=responsibility,
            coverage_area_m2=coverage_area,
            covered_area_m2=covered,
            gap_area_m2=gap,
            overhang_area_m2=overhang,
            coverage_ok=coverage_ok,
        )

    def _control_conflict_issues(self, batch: SurveyBatch) -> list[Issue]:
        """Warn when the batch's basis, or a link it goes through, is withdrawn."""
        issues: list[Issue] = []
        try:
            path = self._path_for_batch(batch)
        except RegistrationError:
            return issues
        for version in _path_versions(batch, path):
            network = self._networks.get(version)
            if network is None or network.status == "active":
                continue
            issues.append(
                Issue(
                    "superseded_control_version",
                    f"batch {batch.id} is projected through {version[0]}@{version[1]}, which "
                    "has been withdrawn. This batch can still be read for audit, but new work "
                    "must use the applicable control version.",
                    severity="warning",
                    details={
                        "batch": batch.id,
                        "network": version[0],
                        "used_version": version[1],
                        "status": network.status,
                    },
                )
            )
        return issues

    def _observation_location_issues(
        self, batch: SurveyBatch, coverage: Sequence[Vec2]
    ) -> list[Issue]:
        issues: list[Issue] = []
        for observation in batch.observations:
            shape = self._to_common(batch, observation.shape)
            inside = all(
                _point_in_convex(point, coverage, tolerance=self.feature_tolerance_m)
                for point in shape
            )
            if not inside:
                issues.append(
                    Issue(
                        "observation_outside_coverage",
                        f"observation {observation.id} falls outside the delivered coverage of "
                        f"batch {batch.id}; keep it for seam review rather than discarding it",
                        severity="warning",
                        details={"observation": observation.id, "batch": batch.id},
                    )
                )
        return issues

    # ------------------------------------------------------------------
    # Seam assessment
    # ------------------------------------------------------------------
    def assess_seam(self, seam_id: str) -> SeamAssessment:
        seam = self.seam(seam_id)
        issues: list[Issue] = []
        control_ok = False
        comparison_ok = False
        primary_id: str | None = None
        comparison_id: str | None = None

        for observation_id in seam.observation_ids:
            if observation_id not in self._observations:
                issues.append(
                    Issue(
                        "unknown_observation",
                        f"seam {seam.id} refers to unregistered observation {observation_id}",
                        details={"observation": observation_id},
                    )
                )
        if issues:
            return SeamAssessment(
                seam_id=seam.id,
                zone_a=seam.zone_a,
                zone_b=seam.zone_b,
                status="blocked",
                decision=None,
                issues=tuple(issues),
                control_ok=False,
                comparison_ok=False,
                primary_id=None,
                comparison_id=None,
            )

        latest = _latest_review(seam)
        decision = latest.decision if latest is not None else seam.decision
        if decision in {"same_object", "two_objects"}:
            status = "reviewed"
        elif decision == "unresolved":
            status = "blocked"
        else:
            status = "open"

        # Control basis: both ends must be tied by registered links that jointly
        # connect in the project common frame.
        batch_ids = {self._observations[item].batch_id for item in seam.observation_ids}
        if not batch_ids:
            issues.append(Issue("invalid_seam", f"seam {seam.id} has no observations"))
        else:
            control_ok, control_issues = self._seam_control_ok(seam, batch_ids, latest)
            issues.extend(control_issues)

        # Pair the seam's own observations by zone so the two ends are explicit.
        by_zone: dict[str, list[FeatureObservation]] = {seam.zone_a: [], seam.zone_b: []}
        for observation_id in seam.observation_ids:
            observation = self._observations[observation_id]
            by_zone.setdefault(observation.zone_id, []).append(observation)

        if decision == "same_object":
            primary_id = (latest.primary_id if latest else None) or seam.primary_id
            comparisons = list(latest.comparison_ids) if latest else []
            if not comparisons and seam.comparison_id:
                comparisons = [seam.comparison_id]
            if not primary_id:
                issues.append(
                    Issue(
                        "missing_primary",
                        f"seam {seam.id} is decided as the same object but no primary survey is named",
                        details={"seam": seam.id},
                    )
                )
            elif primary_id not in seam.observation_ids:
                issues.append(
                    Issue(
                        "primary_not_in_seam",
                        f"primary observation {primary_id} is not part of seam {seam.id}",
                        details={"seam": seam.id, "primary": primary_id},
                    )
                )
            elif control_ok:
                primary = self._observations.get(primary_id)
                if primary is None:
                    issues.append(
                        Issue("unknown_observation", f"primary observation {primary_id} is unknown")
                    )
                else:
                    self._cross_zone_same_object(seam, primary, by_zone, issues)
            if not comparisons:
                issues.append(
                    Issue(
                        "missing_comparison",
                        f"seam {seam.id} decided as one object must retain a comparable resurvey",
                        details={"seam": seam.id},
                    )
                )
            else:
                comparison_id = comparisons[0]
                primary_features = self._observations.get(primary_id) if primary_id else None
                if comparison_id not in seam.observation_ids:
                    issues.append(
                        Issue(
                            "comparison_not_in_seam",
                            f"comparison observation {comparison_id} is not part of seam {seam.id}",
                            details={"seam": seam.id, "comparison": comparison_id},
                        )
                    )
                elif comparison_id == primary_id:
                    issues.append(
                        Issue(
                            "comparison_is_primary",
                            f"seam {seam.id} names the primary survey as its own comparison",
                            details={"seam": seam.id, "observation": comparison_id},
                        )
                    )
                elif primary_features is not None and control_ok:
                    comparison = self._observations[comparison_id]
                    comparison_ok = self._comparable(
                        primary_features, comparison, issues, seam.id
                    )
        elif decision == "two_objects":
            if len(seam.observation_ids) < 2:
                issues.append(
                    Issue(
                        "two_objects_needs_two_surveys",
                        f"seam {seam.id} is decided as two objects but lists "
                        f"{len(seam.observation_ids)} observation(s)",
                        details={"seam": seam.id},
                    )
                )
            if not by_zone[seam.zone_a] or not by_zone[seam.zone_b]:
                issues.append(
                    Issue(
                        "two_objects_missing_side",
                        f"seam {seam.id} must retain measurements from both adjacent zones",
                        details={"seam": seam.id},
                    )
                )
            comparison_ok = True
        elif decision == "unresolved":
            issues.append(
                Issue(
                    "seam_unresolved",
                    f"seam {seam.id} is explicitly unresolved; duplicated geometry cannot "
                    "count toward coverage",
                    details={"seam": seam.id},
                )
            )
        else:
            issues.append(
                Issue(
                    "seam_open",
                    f"seam {seam.id} has no acceptance decision",
                    details={"seam": seam.id},
                )
            )

        return SeamAssessment(
            seam_id=seam.id,
            zone_a=seam.zone_a,
            zone_b=seam.zone_b,
            status=status,
            decision=decision,
            issues=tuple(issues),
            control_ok=control_ok,
            comparison_ok=comparison_ok,
            primary_id=primary_id,
            comparison_id=comparison_id,
        )

    def _cross_zone_same_object(
        self,
        seam: SeamGroup,
        primary: FeatureObservation,
        by_zone: Mapping[str, list[FeatureObservation]],
        issues: list[Issue],
    ) -> None:
        if not by_zone[seam.zone_a] or not by_zone[seam.zone_b]:
            issues.append(
                Issue(
                    "same_object_missing_side",
                    f"seam {seam.id} is decided as one object but one adjacent zone has no "
                    "matched measurement",
                    details={"seam": seam.id},
                )
            )
        primary_batch = self.batch(primary.batch_id)
        primary_shape = self._to_common(primary_batch, primary.shape)
        for observation_id in seam.observation_ids:
            if observation_id == primary.id:
                continue
            other = self._observations[observation_id]
            if other.zone_id == primary.zone_id:
                continue
            other_shape = self._to_common(self.batch(other.batch_id), other.shape)
            if not shape_overlap(
                primary_shape, other_shape, tolerance=self.seam_tolerance_m
            ):
                issues.append(
                    Issue(
                        "same_object_not_comparable",
                        f"primary {primary.id} and {other.id} do not describe the same "
                        f"position within {self.seam_tolerance_m:.3f} m",
                        details={
                            "seam": seam.id,
                            "primary": primary.id,
                            "observation": other.id,
                        },
                    )
                )

    def _comparable(
        self,
        primary: FeatureObservation,
        comparison: FeatureObservation,
        issues: list[Issue],
        seam_id: str,
    ) -> bool:
        primary_shape = self._to_common(self.batch(primary.batch_id), primary.shape)
        comparison_shape = self._to_common(self.batch(comparison.batch_id), comparison.shape)
        if not shape_overlap(
            primary_shape, comparison_shape, tolerance=self.seam_tolerance_m
        ):
            issues.append(
                Issue(
                    "comparison_not_comparable",
                    f"comparison {comparison.id} is not located at primary {primary.id} "
                    f"within {self.seam_tolerance_m:.3f} m",
                    details={"seam": seam_id, "primary": primary.id, "comparison": comparison.id},
                )
            )
            return False
        return True

    def _seam_control_ok(
        self,
        seam: SeamGroup,
        batch_ids: set[str],
        latest: ReviewRecord | None,
    ) -> tuple[bool, list[Issue]]:
        issues: list[Issue] = []
        source_versions = {
            (self._batches[batch_id].network_id, self._batches[batch_id].network_version)
            for batch_id in batch_ids
        }
        if len(source_versions) == 1:
            network = self._require_network(*next(iter(source_versions)))
            if network.status != "active":
                issues.append(
                    Issue(
                        "seam_control_superseded",
                        f"seam {seam.id} relies on superseded network "
                        f"{network.id}@{network.version}",
                        details={"seam": seam.id},
                    )
                )
                return False, issues
            return True, issues

        paths: dict[tuple[str, int], LinkPath] = {}
        common = (self.project.common_network_id, self.project.common_network_version)
        for version in sorted(source_versions):
            network = self._require_network(*version)
            if network.status != "active":
                issues.append(
                    Issue(
                        "seam_control_superseded",
                        f"seam {seam.id} relies on superseded network {network.id}@{network.version}",
                        details={"seam": seam.id, "network": network.id, "version": network.version},
                    )
                )
            try:
                paths[version] = self._links.find_path(version, common)
            except RegistrationError as exc:
                issues.append(
                    Issue(
                        "seam_control_unlinked",
                        f"seam {seam.id}: {exc}",
                        details={"seam": seam.id, **dict(exc.details)},
                    )
                )
        # The two ends must be jointly connected, not merely each floating in
        # a separate frame.
        ordered = sorted(source_versions)
        for left_index, left in enumerate(ordered):
            for right in ordered[left_index + 1 :]:
                try:
                    self._links.find_path(left, right)
                except RegistrationError:
                    issues.append(
                        Issue(
                            "seam_control_disconnected",
                            f"seam {seam.id}: no registered control basis joins "
                            f"{left[0]}@{left[1]} and {right[0]}@{right[1]}",
                            details={
                                "seam": seam.id,
                                "left": list(left),
                                "right": list(right),
                            },
                        )
                    )
        if latest is not None and latest.control_basis:
            try:
                link = self._links.get(latest.control_basis)
            except RegistrationError as exc:
                issues.append(
                    Issue(
                        "unknown_control_basis",
                        f"seam {seam.id} cites unknown control basis {latest.control_basis!r}",
                        details={"seam": seam.id, "control_basis": latest.control_basis},
                    )
                )
            else:
                endpoints = {
                    (link.source_id, link.source_version),
                    (link.target_id, link.target_version),
                }
                relevant = {version for version in source_versions if version not in endpoints}
                if relevant and endpoints.isdisjoint(source_versions):
                    issues.append(
                        Issue(
                            "control_basis_not_applicable",
                            f"control basis {link.id} does not involve either end of seam {seam.id}",
                            details={"seam": seam.id, "control_basis": link.id},
                        )
                    )
        return not any(issue.severity == "error" for issue in issues), issues

    # ------------------------------------------------------------------
    # Partition construction
    # ------------------------------------------------------------------
    def _compose_partition(
        self,
        batch_id: str,
        partition_id: str,
        *,
        edition: int,
        replaces: str | None,
        note: str,
        created_at: str | None,
    ) -> Partition:
        """Build a draft snapshot from live registration state.

        The measured geometry, coverage and transform are frozen into the
        partition.  Feature roles are resolved against the seam decisions that
        are registered *now*; that is what lets an acceptance sheet filed later
        unblock a draft without rewriting the raw measurements.
        """
        batch = self.batch(batch_id)
        zone = self.zone(batch.zone_id)
        diagnostics = self.diagnose_batch(batch_id)
        issues = list(diagnostics.issues)
        if diagnostics.transform is None or diagnostics.coverage is None:
            raise CoverageError(
                f"batch {batch_id} cannot be projected into the common frame",
                code="unlinked_batch",
                details={"issues": [issue.to_dict() for issue in issues]},
            )
        source_network = self._require_network(batch.network_id, batch.network_version)
        if source_network.status != "active":
            issues.append(
                Issue(
                    "superseded_network",
                    f"batch {batch.id} was delivered on {source_network.id}@"
                    f"{source_network.version}, which is {source_network.status}. Redeliver or "
                    "reassociate the batch before cutting a new partition.",
                    details={
                        "batch": batch.id,
                        "network": source_network.id,
                        "version": source_network.version,
                        "status": source_network.status,
                    },
                )
            )
        features, feature_issues = self._partition_features(batch)
        issues.extend(feature_issues)
        pending = [feature for feature in features if feature.role == "pending"]
        if pending:
            issues.append(
                Issue(
                    "seam_unresolved",
                    f"batch {batch.id} contains {len(pending)} feature(s) in an unresolved seam; "
                    "they are retained but cannot count",
                    details={"observations": [feature.observation_id for feature in pending]},
                )
            )
        gap_filled = self._pending_points_in_gap(batch, diagnostics, features)
        if gap_filled:
            issues.append(
                Issue(
                    "duplicate_points_fill_gap",
                    "unreviewed duplicate measurements fall inside a coverage gap; they cannot "
                    "be used to close it",
                    details={"observations": gap_filled, "gap_area_m2": diagnostics.gap_area_m2},
                )
            )
        return Partition(
            id=partition_id,
            zone_id=zone.id,
            batch_id=batch.id,
            edition=edition,
            coverage=diagnostics.coverage,
            features=tuple(features),
            transform=diagnostics.transform,
            coverage_area_m2=diagnostics.coverage_area_m2,
            covered_area_m2=diagnostics.covered_area_m2,
            gap_area_m2=diagnostics.gap_area_m2,
            overhang_area_m2=diagnostics.overhang_area_m2,
            coverage_ok=diagnostics.coverage_ok and not gap_filled,
            status="draft",
            replaces=replaces,
            created_at=created_at,
            note=note,
            issues=tuple(issues),
        )

    def build_partition(
        self,
        batch_id: str,
        *,
        partition_id: str | None = None,
        replaces: str | None = None,
        note: str = "",
        created_at: str | None = None,
    ) -> Partition:
        """Snapshot a registered batch as a draft partition.

        A draft is allowed to carry problems so that a designer can inspect
        them; :meth:`deliver_partition` is what refuses an incomplete snapshot.
        A batch that cannot even be projected into the common frame raises.
        """
        identifier = partition_id or _default_partition_id(batch_id, self._partitions)
        if partition_id is not None and partition_id in self._partitions:
            raise ConflictError(
                f"partition id {partition_id!r} already exists; use refresh or replace",
                code="duplicate_partition",
            )
        partition = self._compose_partition(
            batch_id,
            identifier,
            edition=1,
            replaces=replaces,
            note=note,
            created_at=created_at,
        )
        self._partitions[identifier] = [partition]
        return partition

    def _partition_features(
        self, batch: SurveyBatch
    ) -> tuple[list[PartitionFeature], list[Issue]]:
        features: list[PartitionFeature] = []
        issues: list[Issue] = []
        observation_seam: dict[str, SeamGroup] = {}
        for seam in self._seams.values():
            for observation_id in seam.observation_ids:
                observation_seam[observation_id] = seam
        for observation in batch.observations:
            shape = self._to_common(batch, observation.shape)
            seam = observation_seam.get(observation.id)
            if seam is None:
                features.append(
                    PartitionFeature(
                        observation_id=observation.id,
                        zone_id=observation.zone_id,
                        shape=shape,
                        role="independent",
                        source=observation.source,
                        counted=True,
                    )
                )
                continue
            assessment = self.assess_seam(seam.id)
            self._note_assessment_issues(seam.id, assessment, issues)
            if assessment.decision == "same_object" and assessment.status == "reviewed":
                if observation.id == assessment.primary_id:
                    role, counted = "primary", True
                else:
                    role, counted = "comparison", False
            elif assessment.decision == "two_objects" and assessment.status == "reviewed":
                role, counted = "separate", True
            else:
                role, counted = "pending", False
            features.append(
                PartitionFeature(
                    observation_id=observation.id,
                    zone_id=observation.zone_id,
                    shape=shape,
                    role=role,
                    seam_id=seam.id,
                    source=observation.source,
                    counted=counted,
                )
            )
        return features, issues

    def _note_assessment_issues(
        self, seam_id: str, assessment: SeamAssessment, issues: list[Issue]
    ) -> None:
        for issue in assessment.issues:
            if issue.severity != "error":
                continue
            issues.append(
                Issue(
                    issue.code,
                    issue.message,
                    details={"seam": seam_id, **dict(issue.details)},
                )
            )

    def _pending_points_in_gap(
        self,
        batch: SurveyBatch,
        diagnostics: BatchDiagnostics,
        features: Sequence[PartitionFeature],
    ) -> list[str]:
        """Unreviewed duplicate measurements may not be used to close a gap.

        If a batch misses its registered responsibility area and the only
        measurements sitting in the missing part belong to unresolved seams,
        the gap stays a gap.  The measurements remain visible on the partition
        as ``pending`` evidence, but they do not make the coverage pass.
        """
        if diagnostics.coverage_ok or diagnostics.responsibility is None:
            return []
        from .geometry import convex_difference

        gap_parts = convex_difference(diagnostics.responsibility, diagnostics.coverage)
        result: list[str] = []
        for feature in features:
            if feature.role != "pending":
                continue
            if any(shape_overlap(feature.shape, part) for part in gap_parts):
                result.append(feature.observation_id)
        return result

    # ------------------------------------------------------------------
    # Partition lifecycle
    # ------------------------------------------------------------------
    def partition_readiness(self, partition_id: str, edition: int | None = None) -> PartitionReadiness:
        """Evaluate deliverability against current registration state.

        The frozen geometry of the snapshot is kept, but seam decisions and
        control registrations are read live: an acceptance sheet filed after
        the draft was cut must be able to unblock it.
        """
        partition = self.partition(partition_id, edition)
        issues: list[Issue] = []
        if partition.status != "draft":
            issues.append(
                Issue(
                    "partition_not_draft",
                    f"partition {partition.id} is {partition.status}; only drafts can be changed",
                    severity="warning",
                    details={"status": partition.status},
                )
            )
        diagnostics = self.diagnose_batch(partition.batch_id)
        issues.extend(diagnostics.issues)
        if not partition.coverage_ok or not diagnostics.coverage_ok:
            issues.append(
                Issue(
                    "coverage_gap",
                    f"partition {partition.id} does not cover zone {partition.zone_id}",
                    details={"gap_area_m2": max(partition.gap_area_m2, diagnostics.gap_area_m2)},
                )
            )
        try:
            features, feature_issues = self._partition_features(self.batch(partition.batch_id))
        except LedgerError as exc:
            issues.append(Issue(exc.code, str(exc), details=dict(exc.details)))
            features, feature_issues = [], []
        issues.extend(feature_issues)
        pending = [feature for feature in features if feature.role == "pending"]
        if pending:
            issues.append(
                Issue(
                    "seam_unresolved",
                    f"partition {partition.id} still holds unreviewed seam features",
                    details={"observations": [feature.observation_id for feature in pending]},
                )
            )
        gap_filled = self._pending_points_in_gap(self.batch(partition.batch_id), diagnostics, features)
        if gap_filled:
            issues.append(
                Issue(
                    "duplicate_points_fill_gap",
                    "unreviewed duplicate measurements fall inside a coverage gap",
                    details={"observations": gap_filled},
                )
            )
        stale = self._stale_basis_issue(partition.batch_id)
        if stale is not None:
            issues.append(stale)
        return PartitionReadiness(partition_id=partition.id, issues=tuple(issues))

    def _stale_basis_issue(self, batch_id: str) -> Issue | None:
        """A new delivery must not be cut on a withdrawn control basis."""
        try:
            path = self._path_for_batch(self.batch(batch_id))
        except RegistrationError:
            return None
        for version in _path_versions(self.batch(batch_id), path):
            network = self._networks.get(version)
            if network is not None and network.status != "active":
                return Issue(
                    "superseded_control_basis",
                    f"partition would be cut through {version[0]}@{version[1]}, which is "
                    f"{network.status}. Refresh an undelivered draft or redeliver the batch "
                    "on the applicable control version.",
                    details={
                        "batch": batch_id,
                        "network": version[0],
                        "version": version[1],
                        "status": network.status,
                    },
                )
        return None

    def deliver_partition(self, partition_id: str, edition: int | None = None) -> Partition:
        partition = self.partition(partition_id, edition)
        if partition.status != "draft":
            raise ConflictError(
                f"partition {partition.id} is already {partition.status}", code="partition_not_draft"
            )
        readiness = self.partition_readiness(partition.id, partition.edition)
        blocking = [issue for issue in readiness.issues if issue.severity == "error"]
        if blocking:
            raise SeamError(
                f"partition {partition.id} is not deliverable",
                code="partition_blocked",
                details={"issues": [issue.to_dict() for issue in readiness.issues]},
            )
        # Finalise the draft under exactly the state that just passed readiness.
        final = self._compose_partition(
            partition.batch_id,
            partition.id,
            edition=partition.edition,
            replaces=partition.replaces,
            note=partition.note,
            created_at=partition.created_at,
        )
        residual = [issue for issue in final.issues if issue.severity == "error"]
        if residual or not final.coverage_ok or final.pending_features:
            raise SeamError(
                f"partition {partition.id} changed state while being finalised",
                code="partition_blocked",
                details={"issues": [issue.to_dict() for issue in final.issues]},
            )
        updated = replace(final, status="delivered")
        self._replace_current(updated)
        return updated

    def refresh_draft(
        self,
        partition_id: str,
        *,
        note: str = "",
        created_at: str | None = None,
    ) -> Partition:
        """Rebuild an undelivered partition under the current control basis."""
        partition = self.partition(partition_id)
        if partition.status != "draft":
            raise SeamError(
                f"partition {partition.id} is {partition.status}; a referenced or delivered "
                "partition must not change coordinates in place. Build a replacement instead.",
                code="partition_locked",
                details={"partition": partition.id, "status": partition.status},
            )
        if self.is_referenced(partition.id):
            raise SeamError(
                f"partition {partition.id} is already pinned by a design drawing; a referenced "
                "base map must keep the coordinates the designer used. Build a replacement.",
                code="partition_locked",
                details={"partition": partition.id, "status": partition.status, "pinned": True},
            )
        rebuilt = self._rebuild_from_batch(
            partition.batch_id,
            partition.id,
            edition=partition.edition + 1,
            replaces=partition.replaces,
            note=note or partition.note,
            created_at=created_at,
        )
        return rebuilt

    def replace_partition(
        self,
        partition_id: str,
        *,
        new_id: str | None = None,
        note: str = "",
        created_at: str | None = None,
    ) -> Partition:
        """Create a new partition that supersedes a delivered/pinned one.

        The old snapshot stays readable.  The new partition is a draft until it
        passes readiness and :meth:`deliver_partition`.
        """
        old = self.partition(partition_id)
        if old.status == "draft":
            return self.refresh_draft(old.id, note=note, created_at=created_at)
        candidate_id = new_id or _replacement_id(old.id, self._partitions)
        if candidate_id in self._partitions:
            raise ConflictError(
                f"partition id {candidate_id!r} already exists", code="duplicate_partition"
            )
        rebuilt = self._rebuild_from_batch(
            old.batch_id,
            candidate_id,
            edition=1,
            replaces=old.id,
            note=note or f"replaces {old.id} edition {old.edition}",
            created_at=created_at,
        )
        return rebuilt

    def _rebuild_from_batch(
        self,
        batch_id: str,
        partition_id: str,
        *,
        edition: int,
        replaces: str | None,
        note: str,
        created_at: str | None,
    ) -> Partition:
        partition = self._compose_partition(
            batch_id,
            partition_id,
            edition=edition,
            replaces=replaces,
            note=note,
            created_at=created_at,
        )
        self._partitions.setdefault(partition_id, []).append(partition)
        return partition

    def _replace_current(self, partition: Partition) -> None:
        editions = self._partitions.setdefault(partition.id, [])
        if editions and editions[-1].edition == partition.edition:
            editions[-1] = partition
        else:
            editions.append(partition)

    # ------------------------------------------------------------------
    # Reviews and seams after the fact
    # ------------------------------------------------------------------
    def record_review(self, review: ReviewRecord) -> ReviewRecord:
        if review.id in self._reviews:
            raise ConflictError(
                f"review {review.id!r} is already registered; reviews are append-only",
                code="duplicate_review",
            )
        if review.seam_id is not None:
            seam = self.seam(review.seam_id)
            if review.decision not in {"same_object", "two_objects", "unresolved"}:
                raise RegistrationError(
                    f"unknown seam decision {review.decision!r}", code="invalid_review"
                )
            for observation_id in review.observation_ids:
                if observation_id not in seam.observation_ids:
                    raise RegistrationError(
                        f"review {review.id} cites observation {observation_id} outside seam {seam.id}",
                        code="review_observation_not_in_seam",
                    )
            for observation_id in (review.primary_id, *review.comparison_ids):
                if observation_id is not None and observation_id not in self._observations:
                    raise RegistrationError(
                        f"review {review.id} cites unknown observation {observation_id!r}",
                        code="unknown_observation",
                    )
            status = "reviewed" if review.decision in {"same_object", "two_objects"} else "blocked"
            updated = replace(
                seam,
                reviews=seam.reviews + (review,),
                status=status,
                decision=review.decision,
                primary_id=review.primary_id or seam.primary_id,
                comparison_id=(
                    review.comparison_ids[0] if review.comparison_ids else seam.comparison_id
                ),
            )
            self._seams[seam.id] = updated
        elif review.subject not in self._observations and review.subject not in self._batches:
            raise RegistrationError(
                f"review {review.id} subject {review.subject!r} is neither an observation nor a batch",
                code="unknown_review_subject",
            )
        self._reviews[review.id] = review
        return review

    def reviews_for(self, seam_id: str) -> tuple[ReviewRecord, ...]:
        return self.seam(seam_id).reviews

    # ------------------------------------------------------------------
    # Design references and handover
    # ------------------------------------------------------------------
    def handover(
        self,
        partition_ids: Sequence[str],
        *,
        require_delivered: bool = True,
    ) -> HandoverReport:
        if not partition_ids:
            raise RegistrationError("a handover needs at least one partition", code="empty_handover")
        if len(set(partition_ids)) != len(partition_ids):
            raise ConflictError("a handover repeats a partition id", code="duplicate_partition")
        partitions = [self.partition(item) for item in partition_ids]
        issues: list[Issue] = []
        zones: dict[str, list[Partition]] = {}
        for partition in partitions:
            zones.setdefault(partition.zone_id, []).append(partition)
            if require_delivered and partition.status != "delivered":
                issues.append(
                    Issue(
                        "partition_not_delivered",
                        f"partition {partition.id} is {partition.status}, not delivered",
                        details={"partition": partition.id, "status": partition.status},
                    )
                )
            if not partition.coverage_ok:
                issues.append(
                    Issue(
                        "coverage_gap",
                        f"partition {partition.id} does not cover its zone",
                        details={"partition": partition.id, "gap_area_m2": partition.gap_area_m2},
                    )
                )
            issues.extend(partition.errors)
            if partition.pending_features:
                issues.append(
                    Issue(
                        "seam_unresolved",
                        f"partition {partition.id} still holds unreviewed seam features",
                        details={
                            "partition": partition.id,
                            "observations": [
                                feature.observation_id
                                for feature in partition.pending_features
                            ],
                        },
                    )
                )
        for zone_id, items in zones.items():
            if len(items) > 1:
                issues.append(
                    Issue(
                        "duplicate_zone_partition",
                        f"zone {zone_id} is represented by {len(items)} partitions in the group; "
                        "old and new measurements must not both count",
                        details={"zone": zone_id, "partitions": [item.id for item in items]},
                    )
                )

        self._check_combined_coverage(partitions, issues)
        self._check_combined_features(partitions, issues)
        seam_assessments = self._assess_group_seams(partitions, issues)
        pair_checks = self._pair_checks(partitions, issues)
        group_zones = sorted({partition.zone_id for partition in partitions})
        for issue in self.zone_frame_issues():
            involved = set(issue.details.get("zones", ()))
            if involved and involved <= set(group_zones):
                issues.append(issue)

        counted = []
        for partition in partitions:
            for feature in partition.features:
                if feature.counted:
                    counted.append(feature.observation_id)
        overlap_area = _overlap_area_of_coverages([partition.coverage for partition in partitions])
        combined_gap = self._combined_gap(zones, partitions)

        return HandoverReport(
            partition_ids=tuple(partition.id for partition in partitions),
            issues=tuple(_dedupe_issues(issues)),
            pair_checks=tuple(pair_checks),
            seam_assessments=tuple(seam_assessments),
            combined_gap_area_m2=combined_gap,
            overlap_area_m2=overlap_area,
            counted_feature_ids=tuple(sorted(set(counted))),
        )

    def _check_combined_coverage(
        self, partitions: Sequence[Partition], issues: list[Issue]
    ) -> None:
        seen: dict[str, list[Partition]] = {}
        for partition in partitions:
            seen.setdefault(partition.zone_id, []).append(partition)
        for zone_id, items in seen.items():
            zone = self.zone(zone_id)
            covered = intersection_area_with_union(
                zone.responsibility, [item.coverage for item in items]
            )
            gap = max(0.0, zone.area_m2 - covered)
            if gap > self.coverage_tolerance_m2:
                issues.append(
                    Issue(
                        "combined_coverage_gap",
                        f"the group leaves {gap:.4f} m2 of zone {zone.id} uncovered although each "
                        "partition may pass its own check",
                        details={
                            "zone": zone.id,
                            "gap_area_m2": gap,
                            "partitions": [item.id for item in items],
                        },
                    )
                )

    def _check_combined_features(
        self, partitions: Sequence[Partition], issues: list[Issue]
    ) -> None:
        features: list[tuple[Partition, PartitionFeature]] = [
            (partition, feature)
            for partition in partitions
            for feature in partition.features
            if feature.counted
        ]
        for index, (partition_a, feature_a) in enumerate(features):
            for partition_b, feature_b in features[index + 1 :]:
                if feature_a.observation_id == feature_b.observation_id:
                    if partition_a.id != partition_b.id:
                        issues.append(
                            Issue(
                                "feature_counted_twice",
                                f"observation {feature_a.observation_id} is counted in both "
                                f"{partition_a.id} and {partition_b.id}",
                                details={"observation": feature_a.observation_id},
                            )
                        )
                    continue
                same_seam = (
                    feature_a.seam_id is not None
                    and feature_a.seam_id == feature_b.seam_id
                    and self._seam_two_objects(feature_a.seam_id)
                )
                if not shape_overlap(
                    feature_a.shape, feature_b.shape, tolerance=self.feature_tolerance_m
                ):
                    continue
                if same_seam:
                    continue
                issue_code = (
                    "unregistered_overlap"
                    if feature_a.seam_id is None and feature_b.seam_id is None
                    else "duplicate_feature"
                )
                issues.append(
                    Issue(
                        issue_code,
                        f"features {feature_a.observation_id} and {feature_b.observation_id} "
                        "overlap but no acceptance record says they are two adjacent objects",
                        details={
                            "observations": [
                                feature_a.observation_id,
                                feature_b.observation_id,
                            ],
                            "partitions": [partition_a.id, partition_b.id],
                            "seams": [feature_a.seam_id, feature_b.seam_id],
                        },
                    )
                )

    def _seam_two_objects(self, seam_id: str) -> bool:
        assessment = self.assess_seam(seam_id)
        return assessment.status == "reviewed" and assessment.decision == "two_objects"

    def _assess_group_seams(
        self, partitions: Sequence[Partition], issues: list[Issue]
    ) -> list[SeamAssessment]:
        zone_ids = {partition.zone_id for partition in partitions}
        assessments: list[SeamAssessment] = []
        for seam in sorted(self._seams.values(), key=lambda item: item.id):
            if seam.zone_a not in zone_ids or seam.zone_b not in zone_ids:
                continue
            assessment = self.assess_seam(seam.id)
            assessments.append(assessment)
            for issue in assessment.issues:
                if issue.severity == "error":
                    issues.append(
                        Issue(
                            issue.code,
                            issue.message,
                            details={"seam": seam.id, **dict(issue.details)},
                        )
                    )
        return assessments

    def _pair_checks(
        self, partitions: Sequence[Partition], issues: list[Issue]
    ) -> list[PairCheck]:
        checks: list[PairCheck] = []
        for index, first in enumerate(partitions):
            for second in partitions[index + 1 :]:
                if first.zone_id == second.zone_id:
                    continue
                zone_a = self.zone(first.zone_id)
                zone_b = self.zone(second.zone_id)
                distance = polygon_min_distance(zone_a.responsibility, zone_b.responsibility)
                if distance > self.feature_tolerance_m:
                    continue
                pair_issues: list[Issue] = []
                control_ok = self._pair_control_ok(first, second, pair_issues)
                seam_distance = polygon_min_distance(first.coverage, second.coverage)
                if seam_distance > self.feature_tolerance_m:
                    pair_issues.append(
                        Issue(
                            "seam_coverage_separated",
                            f"adjacent partitions {first.id} and {second.id} leave a "
                            f"{seam_distance:.4f} m gap at the seam",
                            details={
                                "partitions": [first.id, second.id],
                                "distance_m": seam_distance,
                            },
                        )
                    )
                for issue in pair_issues:
                    if issue.severity == "error":
                        issues.append(issue)
                checks.append(
                    PairCheck(
                        partition_a=first.id,
                        partition_b=second.id,
                        zone_a=first.zone_id,
                        zone_b=second.zone_id,
                        coverage_touches=seam_distance <= self.feature_tolerance_m,
                        seam_distance_m=seam_distance,
                        control_ok=control_ok,
                        issues=tuple(pair_issues),
                    )
                )
        return checks

    def _pair_control_ok(
        self, first: Partition, second: Partition, issues: list[Issue]
    ) -> bool:
        batch_a = self.batch(first.batch_id)
        batch_b = self.batch(second.batch_id)
        version_a = (batch_a.network_id, batch_a.network_version)
        version_b = (batch_b.network_id, batch_b.network_version)
        try:
            path = self._links.find_path(version_a, version_b)
        except RegistrationError as exc:
            issues.append(
                Issue(
                    "seam_control_disconnected",
                    f"partitions {first.id} and {second.id} have no joint control basis: {exc}",
                    details={
                        "partitions": [first.id, second.id],
                        "left": list(version_a),
                        "right": list(version_b),
                    },
                )
            )
            return False
        common = (self.project.common_network_id, self.project.common_network_version)
        registered = path.transform
        frozen_a = self._frozen_transform(first)
        frozen_b = self._frozen_transform(second)
        frame_a = (first.transform.target_network_id, first.transform.target_version)
        frame_b = (second.transform.target_network_id, second.transform.target_version)
        if frame_a != frame_b:
            # Coverage polygons are stored as numbers in the frame they were
            # cut in.  Comparing a pair across two frames would silently mix
            # coordinate systems; the designer must re-cut one side (a new
            # partition, not an edit of a pinned one).
            issues.append(
                Issue(
                    "seam_frame_mismatch",
                    f"partitions {first.id} and {second.id} were cut in different common "
                    f"frames ({frame_a[0]}@{frame_a[1]} vs {frame_b[0]}@{frame_b[1]}); "
                    "re-cut one side on the current common frame before joining them",
                    details={
                        "partitions": [first.id, second.id],
                        "frames": [list(frame_a), list(frame_b)],
                    },
                )
            )
            return False
        # Within one frame, what matters at the seam is whether the two ends
        # agree with each other: the relative transform implied by the two
        # snapshots must match the registered basis joining their networks.
        snapshot_relative = compose(invert_similarity(frozen_b), frozen_a)
        disagreement = self._transform_disagreement(snapshot_relative, registered)
        if disagreement > self.seam_tolerance_m:
            issues.append(
                Issue(
                    "control_basis_changed",
                    f"the control basis of partitions {first.id} and {second.id} is no longer "
                    f"jointly valid (relative disagreement {disagreement:.4f} m)",
                    details={
                        "partitions": [first.id, second.id],
                        "disagreement_m": disagreement,
                        "registered_links": [step.link.id for step in path.steps],
                    },
                )
            )
            return False
        return True

    def _frozen_transform(self, partition: Partition) -> Similarity:
        record = partition.transform
        if record.identity:
            return Similarity.identity()
        if record.scale is None or record.rotation_deg is None or record.translation_m is None:
            raise RegistrationError(
                f"partition {partition.id} lacks a complete transform record",
                code="invalid_transform_record",
            )
        from math import radians

        return Similarity(
            record.scale,
            radians(record.rotation_deg),
            Vec2(*record.translation_m),
        )

    def _transform_disagreement(self, left: Similarity, right: Similarity) -> float:
        # Compare at the origin and a 10 m basis; sufficient to expose a changed
        # scale, rotation or translation without unbounded sampling.
        samples = (Vec2(0.0, 0.0), Vec2(10.0, 0.0), Vec2(0.0, 10.0))
        return max(left.apply(point).distance_to(right.apply(point)) for point in samples)

    def pin_design(
        self,
        drawing: str,
        partition_ids: Sequence[str],
        *,
        edition: str,
        note: str = "",
        created_at: str | None = None,
    ) -> DesignReference:
        """Lock a delivered group of adjacent partitions for one drawing.

        A drawing must reference a coherent group: every partition delivered,
        no old and new measurements of the same zone side by side, and the
        control basis at both ends of each seam jointly valid.  Pinning is what
        makes a partition's coordinates historical and immutable.
        """
        if drawing in self._references:
            existing = self._references[drawing]
            if existing.state == "pinned" and tuple(existing.partition_ids) == tuple(partition_ids):
                raise ConflictError(
                    f"drawing {drawing} already pins this partition group as edition "
                    f"{existing.edition}",
                    code="duplicate_design_reference",
                )
        report = self.handover(partition_ids, require_delivered=True)
        if not report.ok:
            raise SeamError(
                f"drawing {drawing} cannot pin the requested partitions",
                code="handover_blocked",
                details={"issues": [issue.to_dict() for issue in report.issues]},
            )
        reference = DesignReference(
            drawing=drawing,
            partition_ids=tuple(partition_ids),
            partition_editions=tuple(
                (partition_id, self.partition(partition_id).edition)
                for partition_id in partition_ids
            ),
            edition=edition,
            state="pinned",
            created_at=created_at,
            note=note,
        )
        self._references[drawing] = reference
        return reference

    def design_reference(self, drawing: str) -> DesignReference:
        try:
            return self._references[drawing]
        except KeyError as exc:
            raise RegistrationError(
                f"unknown drawing {drawing!r}", code="unknown_drawing"
            ) from exc

    def is_referenced(self, partition_id: str) -> bool:
        return any(
            partition_id in reference.partition_ids
            for reference in self._references.values()
            if reference.state == "pinned"
        )

    def release_design(self, drawing: str) -> DesignReference:
        reference = self.design_reference(drawing)
        updated = replace(reference, state="released")
        self._references[drawing] = updated
        return updated

    # ------------------------------------------------------------------
    # Replacement planning / impact
    # ------------------------------------------------------------------
    def affected_seams(self, partition_ids: Sequence[str]) -> tuple[SeamGroup, ...]:
        zones: set[str] = set()
        batches: set[str] = set()
        for partition_id in partition_ids:
            partition = self.partition(partition_id)
            zones.add(partition.zone_id)
            batches.add(partition.batch_id)
        result: list[SeamGroup] = []
        for seam in self._seams.values():
            observations = {self._observations[item] for item in seam.observation_ids if item in self._observations}
            if (
                seam.zone_a in zones
                or seam.zone_b in zones
                or any(observation.batch_id in batches for observation in observations)
            ):
                result.append(seam)
        return tuple(sorted(result, key=lambda item: item.id))

    def replacement_plan(self, partition_ids: Sequence[str]) -> dict:
        """Describe what a set of replacements would touch, without committing.

        Answers the questions asked at handover: which seams does this set
        disturb, which pinned drawings would keep their historical base map,
        and can the proposed new partitions take over from the old group
        together?  A group that individually covers its zones but leaves a seam
        gap or duplicates a feature is reported as not jointly replaceable.
        """
        affected = self.affected_seams(partition_ids)
        zones = sorted({self.partition(item).zone_id for item in partition_ids})
        drawings = sorted(
            reference.drawing
            for reference in self._references.values()
            if reference.state == "pinned"
            and set(partition_ids) & set(reference.partition_ids)
        )
        candidates = [self._preview_rebuild(item) for item in partition_ids]
        preview_issues: list[Issue] = []
        for candidate in candidates:
            if candidate["partition"] is None:
                preview_issues.extend(
                    Issue(issue["code"], issue["message"], severity=issue.get("severity", "error"))
                    for issue in candidate["issues"]
                )
        ready = [item["partition"] for item in candidates if item["partition"] is not None]
        group_issues: list[Issue] = []
        if len(ready) == len(candidates):
            group_issues.extend(self._group_issues(ready))
        return {
            "replaced": list(partition_ids),
            "zones": zones,
            "affected_seams": [seam.id for seam in affected],
            "affected_drawings": drawings,
            "candidates": [
                {
                    "replaces": item["replaces"],
                    "partition": item["partition"].to_dict() if item["partition"] else None,
                    "issues": item["issues"],
                }
                for item in candidates
            ],
            "candidate_ids": [item.id for item in ready],
            "can_replace_together": len(ready) == len(candidates) and not group_issues,
            "preview_issues": [issue.to_dict() for issue in preview_issues],
            "group_issues": [issue.to_dict() for issue in group_issues],
        }

    def _group_issues(self, candidates: Sequence[Partition]) -> list[Issue]:
        """Check the proposed group as a handover in its own right."""
        issues: list[Issue] = []
        by_zone: dict[str, list[Partition]] = {}
        for candidate in candidates:
            by_zone.setdefault(candidate.zone_id, []).append(candidate)
        for zone_id, items in by_zone.items():
            if len(items) > 1:
                issues.append(
                    Issue(
                        "duplicate_zone_partition",
                        f"proposed replacements still put {len(items)} partitions on zone {zone_id}",
                        details={"zone": zone_id, "partitions": [item.id for item in items]},
                    )
                )
            zone = self.zone(zone_id)
            covered = intersection_area_with_union(
                zone.responsibility, [item.coverage for item in items]
            )
            gap = max(0.0, zone.area_m2 - covered)
            if gap > self.coverage_tolerance_m2:
                issues.append(
                    Issue(
                        "combined_coverage_gap",
                        f"proposed replacements leave {gap:.4f} m2 of zone {zone_id} uncovered",
                        details={"zone": zone_id, "gap_area_m2": gap},
                    )
                )
        self._check_combined_features(candidates, issues)
        self._pair_checks(candidates, issues)
        return issues

    def _preview_rebuild(self, partition_id: str) -> dict:
        partition = self.partition(partition_id)
        batch = self.batch(partition.batch_id)
        diagnostics = self.diagnose_batch(batch.id)
        issues = [issue.to_dict() for issue in diagnostics.issues]
        if diagnostics.transform is None or diagnostics.coverage is None or not diagnostics.coverage_ok:
            return {"replaces": partition_id, "partition": None, "issues": issues}
        features, feature_issues = self._partition_features(batch)
        issues.extend(issue.to_dict() for issue in feature_issues)
        pending = [feature for feature in features if feature.role == "pending"]
        if pending:
            issues.append(
                Issue(
                    "seam_unresolved",
                    "rebuilt candidate still holds unreviewed seam features",
                    details={"observations": [feature.observation_id for feature in pending]},
                ).to_dict()
            )
        preview = Partition(
            id=f"{partition_id}#preview",
            zone_id=partition.zone_id,
            batch_id=batch.id,
            edition=1,
            coverage=diagnostics.coverage,
            features=tuple(features),
            transform=diagnostics.transform,
            coverage_area_m2=diagnostics.coverage_area_m2,
            covered_area_m2=diagnostics.covered_area_m2,
            gap_area_m2=diagnostics.gap_area_m2,
            overhang_area_m2=diagnostics.overhang_area_m2,
            coverage_ok=diagnostics.coverage_ok,
            status="draft",
            replaces=partition_id,
            issues=tuple(diagnostics.issues),
        )
        return {
            "replaces": partition_id,
            "partition": preview if not pending else None,
            "issues": issues,
        }

    def _combined_gap(
        self, zones: Mapping[str, list[Partition]], partitions: Sequence[Partition]
    ) -> float:
        total = 0.0
        for zone_id, items in zones.items():
            zone = self.zone(zone_id)
            covered = intersection_area_with_union(
                zone.responsibility, [item.coverage for item in items]
            )
            total += max(0.0, zone.area_m2 - covered)
        return total

    # ------------------------------------------------------------------
    # Traceability
    # ------------------------------------------------------------------
    def trace_partition(self, partition_id: str, edition: int | None = None) -> dict:
        """Everything a designer or auditor needs to retrace a partition."""
        partition = self.partition(partition_id, edition)
        batch = self.batch(partition.batch_id)
        network = self._require_network(batch.network_id, batch.network_version)
        path = self._links.find_path(
            (batch.network_id, batch.network_version),
            (self.project.common_network_id, self.project.common_network_version),
        )
        observations = []
        for feature in partition.features:
            observation = self._observations[feature.observation_id]
            observations.append(
                {
                    "partition_feature": feature.to_dict(),
                    "raw_observation": observation.to_dict(),
                    "raw_unit": network.unit,
                }
            )
        seams = []
        for seam_id in sorted({feature.seam_id for feature in partition.features if feature.seam_id}):
            assessment = self.assess_seam(seam_id)
            seams.append(
                {
                    "seam": self.seam(seam_id).to_dict(),
                    "assessment": assessment.to_dict(),
                }
            )
        references = [
            reference.to_dict()
            for reference in self._references.values()
            if partition.id in reference.partition_ids
        ]
        return {
            "partition": partition.to_dict(),
            "editions": [item.edition for item in self._partitions[partition.id]],
            "zone": self.zone(partition.zone_id).to_dict(),
            "batch": batch.to_dict(),
            "source_network": network.to_dict(),
            "common_network": self._common_network().to_dict(),
            "transform": partition.transform.to_dict(),
            "links": [step.link.to_dict() for step in path.steps],
            "observations": observations,
            "seams": seams,
            "design_references": references,
        }

    def link_lineage(self, source: tuple[str, int], target: tuple[str, int]) -> dict:
        path = self._links.find_path(source, target)
        return {
            "source": list(source),
            "target": list(target),
            "links": [step.link.to_dict() for step in path.steps],
            "transform": {
                "scale": path.transform.scale,
                "rotation_deg": path.transform.rotation_deg,
                "translation_m": path.transform.translation.as_list(),
            },
            "identity": path.identity,
        }

    def dump(self) -> dict:
        """Serialise registration and partition history for review."""
        return {
            "project": {
                "id": self.project.id,
                "name": self.project.name,
                "common_network_id": self.project.common_network_id,
                "common_network_version": self.project.common_network_version,
            },
            "networks": [
                self._networks[key].to_dict() for key in sorted(self._networks)
            ],
            "links": [link.to_dict() for link in self._links.links()],
            "zones": [self._zones[key].to_dict() for key in sorted(self._zones)],
            "batches": [self._batches[key].to_dict() for key in sorted(self._batches)],
            "seams": [self._seams[key].to_dict() for key in sorted(self._seams)],
            "reviews": [self._reviews[key].to_dict() for key in sorted(self._reviews)],
            "partitions": {
                key: [item.to_dict() for item in editions]
                for key, editions in sorted(self._partitions.items())
            },
            "design_references": [
                self._references[key].to_dict() for key in sorted(self._references)
            ],
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _default_partition_id(batch_id: str, existing: Mapping[str, object]) -> str:
    base = f"P-{batch_id}"
    if base not in existing:
        return base
    counter = 2
    while f"{base}-{counter}" in existing:
        counter += 1
    return f"{base}-{counter}"


def _replacement_id(old_id: str, existing: Mapping[str, object]) -> str:
    counter = 2
    while f"{old_id}-r{counter}" in existing:
        counter += 1
    return f"{old_id}-r{counter}"


def _latest_review(seam: SeamGroup) -> ReviewRecord | None:
    return seam.reviews[-1] if seam.reviews else None


def _point_in_convex(point: Vec2, polygon: Sequence[Vec2], *, tolerance: float = 0.0) -> bool:
    count = len(polygon)
    sign = 1 if polygon_area(polygon) > 0 else -1
    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]
        cross = (b - a).cross(point - a) * sign
        if cross < -tolerance * max((b - a).length(), 1e-12):
            return False
    return True


def _path_versions(batch: SurveyBatch, path: LinkPath) -> tuple[tuple[str, int], ...]:
    versions = [(batch.network_id, batch.network_version)]
    for step in path.steps:
        if step.forward:
            versions.append((step.link.target_id, step.link.target_version))
        else:
            versions.append((step.link.source_id, step.link.source_version))
    return tuple(versions)


def _dedupe_issues(issues: Sequence[Issue]) -> list[Issue]:
    seen: set[tuple] = set()
    result: list[Issue] = []
    for issue in issues:
        key = (
            issue.code,
            issue.severity,
            tuple(sorted((str(k), str(v)) for k, v in issue.details.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _overlap_area_of_coverages(coverages: Sequence[Sequence[Vec2]]) -> float:
    if len(coverages) < 2:
        return 0.0
    pieces: list[tuple[Vec2, ...]] = []
    for index, first in enumerate(coverages):
        for second in coverages[index + 1 :]:
            piece = convex_intersection([first, second])
            if piece:
                pieces.append(piece)
    return polygon_union_area(pieces)
