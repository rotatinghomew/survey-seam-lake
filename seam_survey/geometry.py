"""Planar geometry used by the seam ledger.

All operations are deliberately small and deterministic.  The ledger only
accepts convex registered responsibility areas and convex delivered coverage
boundaries; that keeps exact area arithmetic possible without a third-party
geometry engine.  Feature shapes may be polylines or polygons.

Coordinates passed to these helpers are already in the project common frame
(metres).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Iterable, Sequence

from .errors import GeometryError

EPS = 1e-9


@dataclass(frozen=True)
class Vec2:
    x: float
    y: float

    def __add__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, factor: float) -> "Vec2":
        return Vec2(self.x * factor, self.y * factor)

    __rmul__ = __mul__

    def __truediv__(self, factor: float) -> "Vec2":
        return Vec2(self.x / factor, self.y / factor)

    def dot(self, other: "Vec2") -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: "Vec2") -> float:
        return self.x * other.y - self.y * other.x

    def length(self) -> float:
        return hypot(self.x, self.y)

    def distance_to(self, other: "Vec2") -> float:
        return (self - other).length()

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def as_list(self) -> list[float]:
        return [self.x, self.y]


def parse_point(value: Sequence[float], *, label: str = "point") -> Vec2:
    if isinstance(value, Vec2):
        return value
    if hasattr(value, "x") and hasattr(value, "y"):
        return Vec2(float(value.x), float(value.y))
    if len(value) != 2:
        raise GeometryError(f"{label} must have exactly two coordinates")
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise GeometryError(f"{label} coordinates must be numeric") from exc
    if x != x or y != y or x in (float("inf"), float("-inf")) or y in (float("inf"), float("-inf")):
        raise GeometryError(f"{label} coordinates must be finite")
    return Vec2(x, y)


def parse_shape(values: Iterable[Sequence[float]], *, label: str = "shape") -> tuple[Vec2, ...]:
    points = tuple(parse_point(value, label=label) for value in values)
    if len(points) < 2:
        raise GeometryError(f"{label} needs at least two points")
    return points


def parse_polygon(values: Iterable[Sequence[float]], *, label: str = "polygon") -> tuple[Vec2, ...]:
    points = parse_shape(values, label=label)
    if len(points) < 3:
        raise GeometryError(f"{label} needs at least three points")
    if _has_repeated_neighbour(points):
        raise GeometryError(f"{label} has repeated vertices")
    if abs(signed_area(points)) <= EPS:
        raise GeometryError(f"{label} has zero area")
    return points if signed_area(points) > 0 else tuple(reversed(points))


def _has_repeated_neighbour(points: Sequence[Vec2]) -> bool:
    count = len(points)
    for index, point in enumerate(points):
        if point.distance_to(points[(index + 1) % count]) <= EPS:
            return True
    return False


def _coerce(points: Iterable[Sequence[float]]) -> tuple[Vec2, ...]:
    return tuple(parse_point(point) for point in points)


def signed_area(points: Sequence[Vec2]) -> float:
    points = _coerce(points)
    count = len(points)
    if count < 3:
        return 0.0
    total = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % count]
        total += point.cross(nxt)
    return total / 2.0


def polygon_area(points: Sequence[Vec2]) -> float:
    return abs(signed_area(points))


def is_convex(points: Sequence[Vec2]) -> bool:
    """Return True for a simple, consistently wound convex polygon."""
    points = _coerce(points)
    count = len(points)
    if count < 3:
        return False
    sign = 0
    for index in range(count):
        a = points[index]
        b = points[(index + 1) % count]
        c = points[(index + 2) % count]
        turn = (b - a).cross(c - b)
        if abs(turn) <= EPS:
            continue
        current = 1 if turn > 0 else -1
        if sign == 0:
            sign = current
        elif current != sign:
            return False
    return sign != 0


def polygon_centroid(points: Sequence[Vec2]) -> Vec2:
    points = _coerce(points)
    area = signed_area(points)
    if abs(area) <= EPS:
        raise GeometryError("centroid of a zero-area polygon is undefined")
    count = len(points)
    cx = cy = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % count]
        factor = point.cross(nxt)
        cx += (point.x + nxt.x) * factor
        cy += (point.y + nxt.y) * factor
    return Vec2(cx / (6.0 * area), cy / (6.0 * area))


def polygon_bounds(points: Sequence[Vec2]) -> tuple[float, float, float, float]:
    points = _coerce(points)
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_polygon(point: Vec2, polygon: Sequence[Vec2], *, boundary: bool = True) -> bool:
    count = len(polygon)
    inside = False
    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]
        if boundary and point_segment_distance(point, a, b) <= EPS:
            return True
        if (a.y > point.y) != (b.y > point.y):
            crossing_x = a.x + (point.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if crossing_x > point.x:
                inside = not inside
    return inside


def point_segment_distance(point: Vec2, a: Vec2, b: Vec2) -> float:
    ab = b - a
    length_sq = ab.dot(ab)
    if length_sq <= EPS:
        return point.distance_to(a)
    t = max(0.0, min(1.0, (point - a).dot(ab) / length_sq))
    projection = a + ab * t
    return point.distance_to(projection)


def segment_intersection(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> Vec2 | None:
    r = b - a
    s = d - c
    denominator = r.cross(s)
    if abs(denominator) <= EPS:
        return None
    t = (c - a).cross(s) / denominator
    u = (c - a).cross(r) / denominator
    if -EPS <= t <= 1.0 + EPS and -EPS <= u <= 1.0 + EPS:
        return a + r * t
    return None


def segment_segment_distance(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> float:
    if segment_intersection(a, b, c, d) is not None:
        return 0.0
    return min(
        point_segment_distance(a, c, d),
        point_segment_distance(b, c, d),
        point_segment_distance(c, a, b),
        point_segment_distance(d, a, b),
    )


def polygon_min_distance(a: Sequence[Vec2], b: Sequence[Vec2]) -> float:
    a = _coerce(a)
    b = _coerce(b)
    if polygons_touch_or_overlap(a, b):
        return 0.0
    best = float("inf")
    for index in range(len(a)):
        a1, a2 = a[index], a[(index + 1) % len(a)]
        for other in range(len(b)):
            b1, b2 = b[other], b[(other + 1) % len(b)]
            best = min(best, segment_segment_distance(a1, a2, b1, b2))
    return best


def polygons_touch_or_overlap(a: Sequence[Vec2], b: Sequence[Vec2]) -> bool:
    a = _coerce(a)
    b = _coerce(b)
    for index in range(len(a)):
        a1, a2 = a[index], a[(index + 1) % len(a)]
        for other in range(len(b)):
            b1, b2 = b[other], b[(other + 1) % len(b)]
            if segment_intersection(a1, a2, b1, b2) is not None:
                return True
    return point_in_polygon(a[0], b) or point_in_polygon(b[0], a)


def _line_intersection(s: Vec2, e: Vec2, a: Vec2, b: Vec2) -> Vec2:
    r = e - s
    q = b - a
    denominator = r.cross(q)
    if abs(denominator) <= EPS:
        return s
    t = (a - s).cross(q) / denominator
    return s + r * t


def _inside_half_plane(point: Vec2, a: Vec2, b: Vec2, *, ccw: bool) -> bool:
    cross = (b - a).cross(point - a)
    return cross >= -EPS if ccw else cross <= EPS


def clip_convex(subject: Sequence[Vec2], clip: Sequence[Vec2]) -> tuple[Vec2, ...]:
    """Sutherland-Hodgman clip of ``subject`` by a convex ``clip`` polygon."""
    subject = _coerce(subject)
    clip = _coerce(clip)
    if len(subject) < 3 or len(clip) < 3:
        return ()
    clip = tuple(clip) if signed_area(clip) > 0 else tuple(reversed(clip))
    output = list(subject)
    for index in range(len(clip)):
        a = clip[index]
        b = clip[(index + 1) % len(clip)]
        if not output:
            return ()
        inputs = output
        output = []
        previous = inputs[-1]
        previous_inside = _inside_half_plane(previous, a, b, ccw=True)
        for current in inputs:
            current_inside = _inside_half_plane(current, a, b, ccw=True)
            if current_inside:
                if not previous_inside:
                    output.append(_line_intersection(previous, current, a, b))
                output.append(current)
            elif previous_inside:
                output.append(_line_intersection(previous, current, a, b))
            previous, previous_inside = current, current_inside
    cleaned = _dedupe(output)
    if len(cleaned) < 3 or polygon_area(cleaned) <= EPS:
        return ()
    return tuple(cleaned)


def _dedupe(points: Sequence[Vec2]) -> list[Vec2]:
    result: list[Vec2] = []
    for point in points:
        if not result or point.distance_to(result[-1]) > EPS:
            result.append(point)
    if len(result) > 1 and result[0].distance_to(result[-1]) <= EPS:
        result.pop()
    return result


def convex_intersection(polygons: Sequence[Sequence[Vec2]]) -> tuple[Vec2, ...]:
    if not polygons:
        return ()
    result = tuple(polygons[0])
    for polygon in polygons[1:]:
        result = clip_convex(result, polygon)
        if not result:
            return ()
    return result


def intersection_area(a: Sequence[Vec2], b: Sequence[Vec2]) -> float:
    return polygon_area(clip_convex(a, b))


def _clip_single_half_plane(
    subject: Sequence[Vec2], a: Vec2, b: Vec2, *, keep_inside: bool
) -> tuple[Vec2, ...]:
    if not subject:
        return ()
    output: list[Vec2] = []
    previous = subject[-1]
    previous_inside = _inside_half_plane(previous, a, b, ccw=keep_inside)
    for current in subject:
        current_inside = _inside_half_plane(current, a, b, ccw=keep_inside)
        if current_inside:
            if not previous_inside:
                output.append(_line_intersection(previous, current, a, b))
            output.append(current)
        elif previous_inside:
            output.append(_line_intersection(previous, current, a, b))
        previous, previous_inside = current, current_inside
    cleaned = _dedupe(output)
    if len(cleaned) < 3 or polygon_area(cleaned) <= EPS:
        return ()
    return tuple(cleaned)


def convex_difference(subject: Sequence[Vec2], clip: Sequence[Vec2]) -> tuple[tuple[Vec2, ...], ...]:
    """Partition ``subject \\ clip`` into convex pieces (both convex).

    Walking the clip edges keeps the part of the subject outside each edge and
    carries the inside remainder forward.  The pieces are disjoint.
    """
    subject = _coerce(subject)
    clip = _coerce(clip)
    if len(subject) < 3 or len(clip) < 3:
        return ()
    clip = tuple(clip) if signed_area(clip) > 0 else tuple(reversed(clip))
    pieces: list[tuple[Vec2, ...]] = []
    remaining = tuple(subject)
    for index in range(len(clip)):
        a = clip[index]
        b = clip[(index + 1) % len(clip)]
        outside = _clip_single_half_plane(remaining, a, b, keep_inside=False)
        if outside:
            pieces.append(outside)
        remaining = _clip_single_half_plane(remaining, a, b, keep_inside=True)
        if not remaining:
            break
    return tuple(pieces)


def _vertical_interval(polygon: Sequence[Vec2], x: float) -> tuple[float, float] | None:
    crossings: list[float] = []
    for index, point in enumerate(polygon):
        nxt = polygon[(index + 1) % len(polygon)]
        if (point.x < x < nxt.x) or (nxt.x < x < point.x):
            t = (x - point.x) / (nxt.x - point.x)
            crossings.append(point.y + t * (nxt.y - point.y))
    if len(crossings) < 2:
        return None
    crossings.sort()
    return crossings[0], crossings[-1]


def polygon_union_area(polygons: Sequence[Sequence[Vec2]]) -> float:
    """Exact union area of simple polygons by vertical slab decomposition.

    Every vertex x-coordinate and every pairwise edge intersection becomes a
    slab boundary.  Inside a slab the order of the polygon edges cannot change,
    so the union width at the slab midpoint integrates the slab exactly.
    """
    cleaned = [tuple(_coerce(polygon)) for polygon in polygons if len(polygon) >= 3 and polygon_area(polygon) > EPS]
    if not cleaned:
        return 0.0
    xs: list[float] = []
    for polygon in cleaned:
        xs.extend(point.x for point in polygon)
    for first_index, first in enumerate(cleaned):
        for second in cleaned[first_index + 1 :]:
            for a_index in range(len(first)):
                a1, a2 = first[a_index], first[(a_index + 1) % len(first)]
                for b_index in range(len(second)):
                    b1, b2 = second[b_index], second[(b_index + 1) % len(second)]
                    hit = segment_intersection(a1, a2, b1, b2)
                    if hit is not None:
                        xs.append(hit.x)
    xs.sort()
    unique: list[float] = []
    for value in xs:
        if not unique or value - unique[-1] > 1e-10:
            unique.append(value)
    total = 0.0
    for left, right in zip(unique, unique[1:]):
        width = right - left
        if width <= 1e-12:
            continue
        middle = (left + right) / 2.0
        intervals: list[tuple[float, float]] = []
        for polygon in cleaned:
            interval = _vertical_interval(polygon, middle)
            if interval is not None:
                intervals.append(interval)
        if not intervals:
            continue
        intervals.sort()
        covered = 0.0
        current_start, current_end = intervals[0]
        for start, end in intervals[1:]:
            if start <= current_end + EPS:
                current_end = max(current_end, end)
            else:
                covered += current_end - current_start
                current_start, current_end = start, end
        covered += current_end - current_start
        total += width * covered
    return total


def intersection_area_with_union(polygon: Sequence[Vec2], others: Sequence[Sequence[Vec2]]) -> float:
    """Area of ``polygon`` covered by the union of ``others``."""
    remainder: tuple[tuple[Vec2, ...], ...] = (tuple(_coerce(polygon)),)
    for other in others:
        remainder = tuple(piece for part in remainder for piece in convex_difference(part, other))
        if not remainder:
            return polygon_area(polygon)
    return polygon_area(polygon) - sum(polygon_area(piece) for piece in remainder)


def uncovered_area(subject: Sequence[Vec2], coverage: Sequence[Vec2]) -> float:
    """Area of the convex ``subject`` not covered by the convex ``coverage``."""
    pieces = convex_difference(subject, coverage)
    return max(0.0, sum(polygon_area(piece) for piece in pieces))


def polyline_distance(first: Sequence[Vec2], second: Sequence[Vec2]) -> float:
    """Minimum distance between two polylines (each at least two points)."""
    best = float("inf")
    for index in range(len(first) - 1):
        a1, a2 = first[index], first[index + 1]
        for other in range(len(second) - 1):
            b1, b2 = second[other], second[other + 1]
            best = min(best, segment_segment_distance(a1, a2, b1, b2))
    return best


def shape_overlap(shape_a: Sequence[Vec2], shape_b: Sequence[Vec2], *, tolerance: float = 0.0) -> bool:
    """True when two feature shapes touch, cross or fall within tolerance."""
    shape_a = _coerce(shape_a)
    shape_b = _coerce(shape_b)
    if shape_a == shape_b:
        return True
    a_polygon = len(shape_a) >= 3 and abs(signed_area(shape_a)) > EPS
    b_polygon = len(shape_b) >= 3 and abs(signed_area(shape_b)) > EPS
    if a_polygon and b_polygon:
        if polygons_touch_or_overlap(shape_a, shape_b):
            return True
        return polygon_min_distance(shape_a, shape_b) <= tolerance + EPS
    if not a_polygon and not b_polygon:
        return polyline_distance(shape_a, shape_b) <= tolerance + EPS
    polygon, polyline = (shape_a, shape_b) if a_polygon else (shape_b, shape_a)
    if any(point_in_polygon(point, polygon) for point in polyline):
        return True
    minimum = min(
        point_segment_distance(polyline[0], polygon[index], polygon[(index + 1) % len(polygon)])
        for index in range(len(polygon))
    )
    for index in range(len(polyline) - 1):
        for vertex in range(len(polygon)):
            minimum = min(
                minimum,
                segment_segment_distance(
                    polyline[index],
                    polyline[index + 1],
                    polygon[vertex],
                    polygon[(vertex + 1) % len(polygon)],
                ),
            )
    return minimum <= tolerance + EPS


def shape_bounds(shape: Sequence[Vec2]) -> tuple[float, float, float, float]:
    return polygon_bounds(shape)


def bounding_boxes_overlap(
    a: Sequence[Vec2], b: Sequence[Vec2], *, padding: float = 0.0
) -> bool:
    ax0, ay0, ax1, ay1 = shape_bounds(a)
    bx0, by0, bx1, by1 = shape_bounds(b)
    return not (
        ax1 + padding < bx0
        or bx1 + padding < ax0
        or ay1 + padding < by0
        or by1 + padding < ay0
    )
