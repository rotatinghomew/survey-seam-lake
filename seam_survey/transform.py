"""Registered control-network links and similarity transforms."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, sin, sqrt
from typing import Mapping, Sequence

from .errors import RegistrationError, ToleranceError
from .geometry import EPS, Vec2
from .models import ControlNetwork, TransformRecord


@dataclass(frozen=True)
class Similarity:
    """``target = scale * R(rotation) * source + translation`` in metres."""

    scale: float
    rotation_rad: float
    translation: Vec2

    @property
    def rotation_deg(self) -> float:
        return degrees(self.rotation_rad)

    def apply(self, point: Vec2) -> Vec2:
        c = cos(self.rotation_rad) * self.scale
        s = sin(self.rotation_rad) * self.scale
        return Vec2(
            c * point.x - s * point.y + self.translation.x,
            s * point.x + c * point.y + self.translation.y,
        )

    @classmethod
    def identity(cls) -> "Similarity":
        return cls(1.0, 0.0, Vec2(0.0, 0.0))


def fit_similarity(source: Sequence[Vec2], target: Sequence[Vec2]) -> Similarity:
    """Least-squares 2D similarity from corresponding metre coordinates."""
    if len(source) != len(target):
        raise RegistrationError("correspondence lists differ in length", code="invalid_link")
    if len(source) < 2:
        raise RegistrationError(
            "a similarity transform needs at least two corresponding points",
            code="insufficient_common_points",
        )
    count = len(source)
    sx = sum(point.x for point in source) / count
    sy = sum(point.y for point in source) / count
    tx = sum(point.x for point in target) / count
    ty = sum(point.y for point in target) / count
    numerator_real = 0.0
    numerator_imag = 0.0
    denominator = 0.0
    for src, dst in zip(source, target):
        dx = src.x - sx
        dy = src.y - sy
        ex = dst.x - tx
        ey = dst.y - ty
        numerator_real += dx * ex + dy * ey
        numerator_imag += dx * ey - dy * ex
        denominator += dx * dx + dy * dy
    if denominator <= EPS:
        raise RegistrationError(
            "corresponding control points are coincident; scale is undefined",
            code="degenerate_control",
        )
    a_real = numerator_real / denominator
    a_imag = numerator_imag / denominator
    scale = hypot(a_real, a_imag)
    if scale <= EPS:
        raise RegistrationError("fitted scale collapses to zero", code="degenerate_control")
    rotation = atan2(a_imag, a_real)
    translation = Vec2(tx - (a_real * sx - a_imag * sy), ty - (a_imag * sx + a_real * sy))
    return Similarity(scale, rotation, translation)


def residuals_m(
    source: Sequence[Vec2], target: Sequence[Vec2], transform: Similarity
) -> tuple[float, ...]:
    return tuple(transform.apply(src).distance_to(dst) for src, dst in zip(source, target))


@dataclass(frozen=True)
class LinkAssessment:
    """The auditable result of fitting a registered control link."""

    source_network_id: str
    source_version: int
    target_network_id: str
    target_version: int
    point_codes: tuple[str, ...]
    transform: Similarity
    residuals: Mapping[str, float]
    max_residual_m: float
    rms_m: float
    identity: bool

    @property
    def ok(self) -> bool:
        return True

    def record(self) -> TransformRecord:
        return TransformRecord(
            link_id=None,
            source_network_id=self.source_network_id,
            source_version=self.source_version,
            target_network_id=self.target_network_id,
            target_version=self.target_version,
            target_crs="",
            source_unit="m",
            target_unit="m",
            point_codes=self.point_codes,
            scale=self.transform.scale,
            rotation_deg=self.transform.rotation_deg,
            translation_m=self.transform.translation.as_tuple(),
            residuals_m=dict(self.residuals),
            max_residual_m=self.max_residual_m,
            rms_m=self.rms_m,
            identity=self.identity,
        )


@dataclass(frozen=True)
class ControlLink:
    """A registered association between two network versions.

    A link is the *only* permitted way to move between coordinate frames.  It
    is created from same-named registered points and an explicit pair of
    versions; it can never be inferred from nearest neighbours.
    """

    id: str
    source_id: str
    source_version: int
    target_id: str
    target_version: int
    assessment: LinkAssessment
    min_common_points: int
    residual_tolerance_m: float
    max_scale_deviation: float
    note: str = ""
    registered_at: str | None = None

    @property
    def key(self) -> tuple[str, int, str, int]:
        return (self.source_id, self.source_version, self.target_id, self.target_version)

    def to_dict(self) -> dict:
        record = self.assessment.record()
        payload = record.to_dict()
        payload.update(
            {
                "id": self.id,
                "min_common_points": self.min_common_points,
                "residual_tolerance_m": self.residual_tolerance_m,
                "max_scale_deviation": self.max_scale_deviation,
                "note": self.note,
                "registered_at": self.registered_at,
            }
        )
        return payload


class LinkBuilder:
    """Validate two registered network versions and fit their transform."""

    def __init__(
        self,
        *,
        min_common_points: int = 3,
        residual_tolerance_m: float = 0.02,
        max_scale_deviation: float = 0.05,
    ) -> None:
        if min_common_points < 2:
            raise RegistrationError(
                "a control link needs at least two common points", code="invalid_settings"
            )
        if residual_tolerance_m <= 0:
            raise RegistrationError("residual tolerance must be positive", code="invalid_settings")
        if not 0.0 <= max_scale_deviation < 1.0:
            raise RegistrationError("scale deviation bound is invalid", code="invalid_settings")
        self.min_common_points = min_common_points
        self.residual_tolerance_m = residual_tolerance_m
        self.max_scale_deviation = max_scale_deviation

    def assess(self, source: ControlNetwork, target: ControlNetwork) -> LinkAssessment:
        if source.id == target.id and source.version == target.version:
            if source.unit != target.unit:
                raise RegistrationError(
                    "one network version cannot declare two units", code="unit_conflict"
                )
            return LinkAssessment(
                source_network_id=source.id,
                source_version=source.version,
                target_network_id=target.id,
                target_version=target.version,
                point_codes=tuple(sorted(source.point_codes)),
                transform=Similarity.identity(),
                residuals={code: 0.0 for code in sorted(source.point_codes)},
                max_residual_m=0.0,
                rms_m=0.0,
                identity=True,
            )
        shared = tuple(sorted(source.point_codes & target.point_codes))
        if len(shared) < self.min_common_points:
            raise RegistrationError(
                f"networks {source.id}@{source.version} and {target.id}@{target.version} share "
                f"{len(shared)} registered point codes; {self.min_common_points} are required",
                code="insufficient_common_points",
                details={
                    "shared": list(shared),
                    "required": self.min_common_points,
                    "source_points": sorted(source.point_codes),
                    "target_points": sorted(target.point_codes),
                },
            )
        source_points = [source.metres(code) for code in shared]
        target_points = [target.metres(code) for code in shared]
        transform = fit_similarity(source_points, target_points)
        residual_values = residuals_m(source_points, target_points, transform)
        residual_map = dict(zip(shared, residual_values))
        max_residual = max(residual_values)
        rms = sqrt(sum(value * value for value in residual_values) / len(residual_values))
        if abs(transform.scale - 1.0) > self.max_scale_deviation:
            raise ToleranceError(
                f"fitted scale {transform.scale:.6f} between {source.id} and {target.id} "
                f"deviates from 1 by more than {self.max_scale_deviation:.1%}",
                code="scale_conflict",
                details={
                    "scale": transform.scale,
                    "allowed_deviation": self.max_scale_deviation,
                    "source_unit": source.unit,
                    "target_unit": target.unit,
                },
            )
        if max_residual > self.residual_tolerance_m:
            raise ToleranceError(
                f"control residuals between {source.id} and {target.id} reach "
                f"{max_residual:.4f} m (limit {self.residual_tolerance_m:.4f} m)",
                code="residual_exceeded",
                details={
                    "residuals_m": residual_map,
                    "max_residual_m": max_residual,
                    "tolerance_m": self.residual_tolerance_m,
                },
            )
        return LinkAssessment(
            source_network_id=source.id,
            source_version=source.version,
            target_network_id=target.id,
            target_version=target.version,
            point_codes=shared,
            transform=transform,
            residuals=residual_map,
            max_residual_m=max_residual,
            rms_m=rms,
            identity=False,
        )
