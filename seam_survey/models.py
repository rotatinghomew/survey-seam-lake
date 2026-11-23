"""Domain records for control networks, zones, batches and partitions."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from .errors import ConflictError, RegistrationError
from .geometry import (
    Vec2,
    is_convex,
    parse_point,
    parse_polygon,
    polygon_area,
    polygon_centroid,
    signed_area,
)

UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0}


def unit_factor(unit: str) -> float:
    try:
        return UNIT_TO_M[unit]
    except KeyError as exc:
        raise RegistrationError(f"unsupported unit {unit!r}", code="unsupported_unit") from exc


def _point_map(values: Mapping[str, Sequence[float]], *, label: str) -> dict[str, tuple[float, float]]:
    result: dict[str, tuple[float, float]] = {}
    for code, raw in values.items():
        if not isinstance(code, str) or not code.strip():
            raise RegistrationError(f"{label} point codes must be non-empty", code="invalid_point_code")
        point = parse_point(raw, label=f"{label} point {code}")
        if code in result:
            raise ConflictError(f"{label} registers point {code!r} twice", code="duplicate_point_code")
        result[code] = point.as_tuple()
    if not result:
        raise RegistrationError(f"{label} has no control points", code="missing_control_points")
    return result


@dataclass(frozen=True)
class Project:
    """A survey project whose common frame is one registered network version."""

    id: str
    name: str
    common_network_id: str
    common_network_version: int

    def __post_init__(self) -> None:
        if not self.id:
            raise RegistrationError("project id is required", code="missing_id")
        if not self.common_network_id:
            raise RegistrationError("common network id is required", code="missing_common_network")
        if self.common_network_version < 1:
            raise RegistrationError(
                "common network version must be >= 1", code="invalid_version"
            )


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "error"  # error or warning
    details: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warning"}:
            raise RegistrationError(f"unknown issue severity {self.severity!r}", code="invalid_severity")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class BatchDiagnostics:
    batch_id: str
    issues: tuple[Issue, ...]
    transform: "TransformRecord | None"
    coverage: tuple[Vec2, ...] | None
    responsibility: tuple[Vec2, ...] | None
    coverage_area_m2: float
    covered_area_m2: float
    gap_area_m2: float
    overhang_area_m2: float
    coverage_ok: bool

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def ready(self) -> bool:
        return not self.errors and self.coverage_ok

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "issues": [issue.to_dict() for issue in self.issues],
            "transform": self.transform.to_dict() if self.transform else None,
            "coverage": [point.as_list() for point in self.coverage] if self.coverage else None,
            "responsibility": (
                [point.as_list() for point in self.responsibility] if self.responsibility else None
            ),
            "coverage_area_m2": self.coverage_area_m2,
            "covered_area_m2": self.covered_area_m2,
            "gap_area_m2": self.gap_area_m2,
            "overhang_area_m2": self.overhang_area_m2,
            "coverage_ok": self.coverage_ok,
            "ready": self.ready,
        }


@dataclass(frozen=True)
class SeamAssessment:
    seam_id: str
    zone_a: str
    zone_b: str
    status: str
    decision: str | None
    issues: tuple[Issue, ...]
    control_ok: bool
    comparison_ok: bool
    primary_id: str | None
    comparison_id: str | None

    @property
    def resolved(self) -> bool:
        return self.status == "reviewed" and self.decision in {"same_object", "two_objects"}

    @property
    def ready(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues) and self.resolved

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    def to_dict(self) -> dict:
        return {
            "seam_id": self.seam_id,
            "zone_a": self.zone_a,
            "zone_b": self.zone_b,
            "status": self.status,
            "decision": self.decision,
            "issues": [issue.to_dict() for issue in self.issues],
            "control_ok": self.control_ok,
            "comparison_ok": self.comparison_ok,
            "primary_id": self.primary_id,
            "comparison_id": self.comparison_id,
            "resolved": self.resolved,
            "ready": self.ready,
        }


@dataclass(frozen=True)
class PairCheck:
    partition_a: str
    partition_b: str
    zone_a: str
    zone_b: str
    coverage_touches: bool
    seam_distance_m: float
    control_ok: bool
    issues: tuple[Issue, ...]

    def to_dict(self) -> dict:
        return {
            "partition_a": self.partition_a,
            "partition_b": self.partition_b,
            "zone_a": self.zone_a,
            "zone_b": self.zone_b,
            "coverage_touches": self.coverage_touches,
            "seam_distance_m": self.seam_distance_m,
            "control_ok": self.control_ok,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class HandoverReport:
    partition_ids: tuple[str, ...]
    issues: tuple[Issue, ...]
    pair_checks: tuple[PairCheck, ...]
    seam_assessments: tuple[SeamAssessment, ...]
    combined_gap_area_m2: float
    overlap_area_m2: float
    counted_feature_ids: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "partition_ids": list(self.partition_ids),
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "pair_checks": [check.to_dict() for check in self.pair_checks],
            "seam_assessments": [item.to_dict() for item in self.seam_assessments],
            "combined_gap_area_m2": self.combined_gap_area_m2,
            "overlap_area_m2": self.overlap_area_m2,
            "counted_feature_ids": list(self.counted_feature_ids),
        }


@dataclass(frozen=True)
class PartitionReadiness:
    partition_id: str
    issues: tuple[Issue, ...]

    @property
    def ready(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    def to_dict(self) -> dict:
        return {
            "partition_id": self.partition_id,
            "ready": self.ready,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class ControlNetwork:
    """A registered control-network deliverable.

    ``points`` are exactly as delivered, in ``unit``.  They are never
    re-associated by proximity: only the registered point code may be used to
    link two networks.
    """

    id: str
    crs: str
    unit: str
    version: int
    points: Mapping[str, tuple[float, float]]
    status: str = "active"
    approved: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise RegistrationError("control network id is required", code="missing_id")
        if self.version < 1:
            raise RegistrationError("control network version must be >= 1", code="invalid_version")
        if self.unit not in UNIT_TO_M:
            raise RegistrationError(f"unsupported unit {self.unit!r}", code="unsupported_unit")
        if self.status not in {"active", "superseded"}:
            raise RegistrationError(f"unknown network status {self.status!r}", code="invalid_status")
        object.__setattr__(self, "points", _point_map(self.points, label=f"network {self.id}"))

    @property
    def point_codes(self) -> frozenset[str]:
        return frozenset(self.points)

    @property
    def metre_factor(self) -> float:
        return unit_factor(self.unit)

    def metres(self, code: str) -> Vec2:
        try:
            raw = self.points[code]
        except KeyError as exc:
            raise RegistrationError(
                f"point {code!r} is not registered in network {self.id}", code="unknown_point_code"
            ) from exc
        return parse_point(raw) * self.metre_factor

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "crs": self.crs,
            "unit": self.unit,
            "version": self.version,
            "common_points": {code: list(raw) for code, raw in self.points.items()},
            "status": self.status,
            "approved": self.approved,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "ControlNetwork":
        points = data.get("common_points", data.get("points", {}))
        return cls(
            id=data["id"],
            crs=data["crs"],
            unit=data["unit"],
            version=int(data.get("version", 1)),
            points=points,
            status=data.get("status", "active"),
            approved=bool(data.get("approved", True)),
            note=data.get("note", ""),
        )

    def superseded(self, note: str = "") -> "ControlNetwork":
        return replace(self, status="superseded", note=note or self.note)


@dataclass(frozen=True)
class Zone:
    """A pre-registered responsibility area in the common frame."""

    id: str
    owner: str
    responsibility: tuple[Vec2, ...]
    target_area_m2: float | None = None
    start_m: float | None = None
    end_m: float | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise RegistrationError("zone id is required", code="missing_id")
        polygon = parse_polygon(self.responsibility, label=f"zone {self.id}")
        if not is_convex(polygon):
            raise RegistrationError(
                f"zone {self.id} responsibility area must be convex", code="non_convex_zone"
            )
        object.__setattr__(self, "responsibility", polygon)
        if self.target_area_m2 is not None and self.target_area_m2 < 0:
            raise RegistrationError("zone target area cannot be negative", code="invalid_area")

    @property
    def area_m2(self) -> float:
        return polygon_area(self.responsibility)

    @property
    def centroid(self) -> Vec2:
        return polygon_centroid(self.responsibility)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "owner": self.owner,
            "responsibility": [point.as_list() for point in self.responsibility],
            "target_area_m2": self.target_area_m2,
            "start_m": self.start_m,
            "end_m": self.end_m,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "Zone":
        polygon = data.get("responsibility") or data.get("points")
        if not polygon:
            raise RegistrationError(f"zone {data.get('id')} has no responsibility area", code="missing_zone")
        return cls(
            id=data["id"],
            owner=data.get("owner", ""),
            responsibility=tuple(parse_point(value) for value in polygon),
            target_area_m2=data.get("target_area_m2", data.get("coverage_target_m2")),
            start_m=data.get("start_m"),
            end_m=data.get("end_m"),
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class ReviewRecord:
    """An acceptance-sheet entry.  Records are append-only."""

    id: str
    subject: str
    decision: str
    reviewer: str = ""
    note: str = ""
    observation_ids: tuple[str, ...] = ()
    primary_id: str | None = None
    comparison_ids: tuple[str, ...] = ()
    seam_id: str | None = None
    control_basis: str | None = None
    recorded_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise RegistrationError("review id is required", code="missing_id")
        if not self.subject:
            raise RegistrationError("review subject is required", code="missing_subject")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "decision": self.decision,
            "reviewer": self.reviewer,
            "note": self.note,
            "observation_ids": list(self.observation_ids),
            "primary_id": self.primary_id,
            "comparison_ids": list(self.comparison_ids),
            "seam_id": self.seam_id,
            "control_basis": self.control_basis,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "ReviewRecord":
        return cls(
            id=data["id"],
            subject=data["subject"],
            decision=data["decision"],
            reviewer=data.get("reviewer", ""),
            note=data.get("note", ""),
            observation_ids=tuple(data.get("observation_ids", ())),
            primary_id=data.get("primary_id"),
            comparison_ids=tuple(data.get("comparison_ids", ())),
            seam_id=data.get("seam_id"),
            control_basis=data.get("control_basis"),
            recorded_at=data.get("recorded_at"),
        )


@dataclass(frozen=True)
class FeatureObservation:
    """A raw feature measurement in its batch network's unit."""

    id: str
    zone_id: str
    batch_id: str
    shape: tuple[Vec2, ...]
    source: str = ""
    review: str = "pending"
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise RegistrationError("observation id is required", code="missing_id")
        shape = tuple(parse_point(point) for point in self.shape)
        if len(shape) < 2:
            raise RegistrationError(
                f"observation {self.id} needs at least two points", code="invalid_observation"
            )
        if self.review not in {"pending", "accepted", "rejected", "superseded"}:
            raise RegistrationError(
                f"unknown observation review state {self.review!r}", code="invalid_review"
            )
        object.__setattr__(self, "shape", shape)

    @property
    def is_polygon(self) -> bool:
        return len(self.shape) >= 3 and abs(signed_area(self.shape)) > 1e-9

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "zone": self.zone_id,
            "batch": self.batch_id,
            "shape": [point.as_list() for point in self.shape],
            "source": self.source,
            "review": self.review,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "FeatureObservation":
        return cls(
            id=data["id"],
            zone_id=data.get("zone", data.get("zone_id", "")),
            batch_id=data.get("batch", data.get("batch_id", "")),
            shape=tuple(parse_point(value) for value in data["shape"]),
            source=data.get("source", ""),
            review=data.get("review", "pending"),
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class SurveyBatch:
    """One team's delivered round, tied to one registered network version."""

    id: str
    zone_id: str
    network_id: str
    network_version: int
    coverage: tuple[Vec2, ...]
    observations: tuple[FeatureObservation, ...] = ()
    received_at: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise RegistrationError("batch id is required", code="missing_id")
        polygon = parse_polygon(self.coverage, label=f"batch {self.id} coverage")
        if not is_convex(polygon):
            raise RegistrationError(
                f"batch {self.id} coverage must be convex", code="non_convex_coverage"
            )
        object.__setattr__(self, "coverage", polygon)
        ids = [observation.id for observation in self.observations]
        if len(ids) != len(set(ids)):
            raise ConflictError(f"batch {self.id} repeats an observation id", code="duplicate_observation")
        for observation in self.observations:
            if observation.batch_id != self.id:
                raise RegistrationError(
                    f"observation {observation.id} belongs to batch {observation.batch_id}, not {self.id}",
                    code="observation_batch_mismatch",
                )
            if observation.zone_id != self.zone_id:
                raise RegistrationError(
                    f"observation {observation.id} belongs to zone {observation.zone_id}, not {self.zone_id}",
                    code="observation_zone_mismatch",
                )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "zone_id": self.zone_id,
            "network_id": self.network_id,
            "network_version": self.network_version,
            "coverage": [point.as_list() for point in self.coverage],
            "observations": [item.to_dict() for item in self.observations],
            "received_at": self.received_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SurveyBatch":
        return cls(
            id=data["id"],
            zone_id=data["zone_id"],
            network_id=data["network_id"],
            network_version=int(data.get("network_version", 1)),
            coverage=tuple(parse_point(value) for value in data["coverage"]),
            observations=tuple(
                FeatureObservation.from_dict(item) for item in data.get("observations", ())
            ),
            received_at=data.get("received_at"),
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class SeamGroup:
    """A registered suspicion where two zones may have measured one object twice."""

    id: str
    zone_a: str
    zone_b: str
    observation_ids: tuple[str, ...]
    status: str = "open"
    decision: str | None = None
    primary_id: str | None = None
    comparison_id: str | None = None
    reviews: tuple[ReviewRecord, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise RegistrationError("seam id is required", code="missing_id")
        if self.zone_a == self.zone_b:
            raise RegistrationError("a seam must join two different zones", code="invalid_seam")
        if len(set(self.observation_ids)) != len(self.observation_ids):
            raise ConflictError(f"seam {self.id} repeats an observation", code="duplicate_observation")
        if self.status not in {"open", "reviewed", "blocked"}:
            raise RegistrationError(f"unknown seam status {self.status!r}", code="invalid_seam_status")
        if self.decision not in {None, "same_object", "two_objects", "unresolved"}:
            raise RegistrationError(f"unknown seam decision {self.decision!r}", code="invalid_seam_decision")

    @property
    def resolved(self) -> bool:
        return self.status == "reviewed" and self.decision in {"same_object", "two_objects"}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "zone_a": self.zone_a,
            "zone_b": self.zone_b,
            "observation_ids": list(self.observation_ids),
            "status": self.status,
            "decision": self.decision,
            "primary_id": self.primary_id,
            "comparison_id": self.comparison_id,
            "reviews": [item.to_dict() for item in self.reviews],
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SeamGroup":
        return cls(
            id=data["id"],
            zone_a=data["zone_a"],
            zone_b=data["zone_b"],
            observation_ids=tuple(data.get("observation_ids", ())),
            status=data.get("status", "open"),
            decision=data.get("decision"),
            primary_id=data.get("primary_id"),
            comparison_id=data.get("comparison_id"),
            reviews=tuple(ReviewRecord.from_dict(item) for item in data.get("reviews", ())),
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class PartitionFeature:
    """A feature as it appears on a delivered partition."""

    observation_id: str
    zone_id: str
    shape: tuple[Vec2, ...]
    role: str = "primary"  # primary, comparison, separate, independent, pending
    seam_id: str | None = None
    source: str = ""
    counted: bool = True

    def __post_init__(self) -> None:
        if self.role not in {"primary", "comparison", "separate", "independent", "pending"}:
            raise RegistrationError(f"unknown feature role {self.role!r}", code="invalid_feature_role")
        if self.role == "comparison" and self.counted:
            raise RegistrationError(
                "a comparison survey is evidence and must not be counted as a separate object",
                code="comparison_counted",
            )
        if self.role == "pending" and self.counted:
            raise RegistrationError(
                "a feature in an unresolved seam cannot be counted",
                code="pending_counted",
            )

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "zone_id": self.zone_id,
            "shape": [point.as_list() for point in self.shape],
            "role": self.role,
            "seam_id": self.seam_id,
            "source": self.source,
            "counted": self.counted,
        }


@dataclass(frozen=True)
class TransformRecord:
    """Auditable transformation basis captured into a partition."""

    link_id: str | None
    source_network_id: str
    source_version: int
    target_network_id: str
    target_version: int
    target_crs: str
    source_unit: str
    target_unit: str
    point_codes: tuple[str, ...]
    scale: float | None
    rotation_deg: float | None
    translation_m: tuple[float, float] | None
    residuals_m: Mapping[str, float]
    max_residual_m: float | None
    rms_m: float | None
    identity: bool = False
    link_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "link_id": self.link_id,
            "link_ids": list(self.link_ids),
            "source_network_id": self.source_network_id,
            "source_version": self.source_version,
            "target_network_id": self.target_network_id,
            "target_version": self.target_version,
            "target_crs": self.target_crs,
            "source_unit": self.source_unit,
            "target_unit": self.target_unit,
            "point_codes": list(self.point_codes),
            "scale": self.scale,
            "rotation_deg": self.rotation_deg,
            "translation_m": list(self.translation_m) if self.translation_m else None,
            "residuals_m": dict(self.residuals_m),
            "max_residual_m": self.max_residual_m,
            "rms_m": self.rms_m,
            "identity": self.identity,
        }


@dataclass(frozen=True)
class Partition:
    """An immutable, deliverable snapshot of one batch in the common frame."""

    id: str
    zone_id: str
    batch_id: str
    edition: int
    coverage: tuple[Vec2, ...]
    features: tuple[PartitionFeature, ...]
    transform: TransformRecord
    coverage_area_m2: float
    covered_area_m2: float
    gap_area_m2: float
    overhang_area_m2: float
    coverage_ok: bool
    status: str = "draft"  # draft, delivered, superseded
    replaces: str | None = None
    created_at: str | None = None
    note: str = ""
    issues: tuple[Issue, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise RegistrationError("partition id is required", code="missing_id")
        if self.edition < 1:
            raise RegistrationError("partition edition must be >= 1", code="invalid_edition")
        if self.status not in {"draft", "delivered", "superseded"}:
            raise RegistrationError(f"unknown partition status {self.status!r}", code="invalid_status")

    @property
    def centroid(self) -> Vec2:
        return polygon_centroid(self.coverage)

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def pending_features(self) -> tuple[PartitionFeature, ...]:
        return tuple(feature for feature in self.features if feature.role == "pending")

    @property
    def ready(self) -> bool:
        return self.status == "draft" and self.coverage_ok and not self.errors and not self.pending_features

    @property
    def counted_features(self) -> tuple[PartitionFeature, ...]:
        return tuple(feature for feature in self.features if feature.counted)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "zone_id": self.zone_id,
            "batch_id": self.batch_id,
            "edition": self.edition,
            "coverage": [point.as_list() for point in self.coverage],
            "features": [item.to_dict() for item in self.features],
            "transform": self.transform.to_dict(),
            "coverage_area_m2": self.coverage_area_m2,
            "covered_area_m2": self.covered_area_m2,
            "gap_area_m2": self.gap_area_m2,
            "overhang_area_m2": self.overhang_area_m2,
            "coverage_ok": self.coverage_ok,
            "status": self.status,
            "replaces": self.replaces,
            "created_at": self.created_at,
            "note": self.note,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "Partition":
        transform = data["transform"]
        return cls(
            id=data["id"],
            zone_id=data["zone_id"],
            batch_id=data["batch_id"],
            edition=int(data["edition"]),
            coverage=tuple(parse_point(value) for value in data["coverage"]),
            features=tuple(
                PartitionFeature(
                    observation_id=item["observation_id"],
                    zone_id=item["zone_id"],
                    shape=tuple(parse_point(value) for value in item["shape"]),
                    role=item.get("role", "primary"),
                    seam_id=item.get("seam_id"),
                    source=item.get("source", ""),
                    counted=bool(item.get("counted", True)),
                )
                for item in data.get("features", ())
            ),
            transform=TransformRecord(
                link_id=transform.get("link_id"),
                source_network_id=transform["source_network_id"],
                source_version=int(transform["source_version"]),
                target_network_id=transform["target_network_id"],
                target_version=int(transform["target_version"]),
                target_crs=transform.get("target_crs", ""),
                source_unit=transform.get("source_unit", "m"),
                target_unit=transform.get("target_unit", "m"),
                point_codes=tuple(transform.get("point_codes", ())),
                scale=transform.get("scale"),
                rotation_deg=transform.get("rotation_deg"),
                translation_m=(
                    tuple(transform["translation_m"]) if transform.get("translation_m") else None
                ),
                residuals_m=dict(transform.get("residuals_m", {})),
                max_residual_m=transform.get("max_residual_m"),
                rms_m=transform.get("rms_m"),
                identity=bool(transform.get("identity", False)),
                link_ids=tuple(transform.get("link_ids", ())),
            ),
            coverage_area_m2=float(data["coverage_area_m2"]),
            covered_area_m2=float(data["covered_area_m2"]),
            gap_area_m2=float(data["gap_area_m2"]),
            overhang_area_m2=float(data["overhang_area_m2"]),
            coverage_ok=bool(data["coverage_ok"]),
            status=data.get("status", "draft"),
            replaces=data.get("replaces"),
            created_at=data.get("created_at"),
            note=data.get("note", ""),
            issues=tuple(Issue(**row) for row in data.get("issues", ())),
        )


@dataclass(frozen=True)
class DesignReference:
    drawing: str
    partition_ids: tuple[str, ...]
    edition: str
    state: str = "pinned"  # pinned, released
    created_at: str | None = None
    note: str = ""
    partition_editions: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if not self.drawing:
            raise RegistrationError("drawing id is required", code="missing_id")
        if self.state not in {"pinned", "released"}:
            raise RegistrationError(f"unknown design reference state {self.state!r}", code="invalid_state")

    def edition_of(self, partition_id: str) -> int | None:
        for identifier, edition in self.partition_editions:
            if identifier == partition_id:
                return edition
        return None

    def to_dict(self) -> dict:
        return {
            "drawing": self.drawing,
            "partition_ids": list(self.partition_ids),
            "partition_editions": [list(item) for item in self.partition_editions],
            "edition": self.edition,
            "state": self.state,
            "created_at": self.created_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "DesignReference":
        return cls(
            drawing=data["drawing"],
            partition_ids=tuple(data.get("partition_ids", ())),
            edition=data.get("edition", ""),
            state=data.get("state", "pinned"),
            created_at=data.get("created_at"),
            note=data.get("note", ""),
            partition_editions=tuple(
                (item[0], int(item[1])) for item in data.get("partition_editions", ())
            ),
        )
