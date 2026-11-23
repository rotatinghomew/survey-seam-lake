"""Coordinate lineage: a chain of registered links, not proximity joins."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .errors import RegistrationError
from .geometry import Vec2
from .models import TransformRecord
from .transform import ControlLink, Similarity


@dataclass(frozen=True)
class LinkStep:
    link: ControlLink
    forward: bool

    @property
    def similarity(self) -> Similarity:
        transform = self.link.assessment.transform
        return transform if self.forward else invert_similarity(transform)


def invert_similarity(transform: Similarity) -> Similarity:
    scale = 1.0 / transform.scale
    rotation = -transform.rotation_rad
    from math import cos, sin

    tx = transform.translation.x
    ty = transform.translation.y
    inverse_translation = Vec2(
        -(scale * cos(rotation) * tx - scale * sin(rotation) * ty),
        -(scale * sin(rotation) * tx + scale * cos(rotation) * ty),
    )
    return Similarity(scale, rotation, inverse_translation)


@dataclass(frozen=True)
class LinkPath:
    """Result of walking registered links from a source version to a target."""

    source: tuple[str, int]
    target: tuple[str, int]
    steps: tuple[LinkStep, ...]
    transform: Similarity

    @property
    def identity(self) -> bool:
        return not self.steps

    def record(self, *, source_unit: str, target_unit: str, target_crs: str) -> TransformRecord:
        if self.identity:
            return TransformRecord(
                link_id=None,
                source_network_id=self.source[0],
                source_version=self.source[1],
                target_network_id=self.target[0],
                target_version=self.target[1],
                target_crs=target_crs,
                source_unit=source_unit,
                target_unit=target_unit,
                point_codes=(),
                scale=1.0,
                rotation_deg=0.0,
                translation_m=(0.0, 0.0),
                residuals_m={},
                max_residual_m=0.0,
                rms_m=0.0,
                identity=True,
            )
        last = self.steps[-1].link
        assessment = last.assessment
        residuals: dict[str, float] = {}
        for step in self.steps:
            for code, value in step.link.assessment.residuals.items():
                residuals[f"{step.link.id}:{code}"] = value
        return TransformRecord(
            link_id=">".join(step.link.id for step in self.steps),
            source_network_id=self.source[0],
            source_version=self.source[1],
            target_network_id=self.target[0],
            target_version=self.target[1],
            target_crs=target_crs,
            source_unit=source_unit,
            target_unit=target_unit,
            point_codes=assessment.point_codes,
            scale=self.transform.scale,
            rotation_deg=self.transform.rotation_deg,
            translation_m=self.transform.translation.as_tuple(),
            residuals_m=residuals,
            max_residual_m=max(
                (step.link.assessment.max_residual_m for step in self.steps), default=0.0
            ),
            rms_m=max((step.link.assessment.rms_m for step in self.steps), default=0.0),
            identity=False,
        )


class LinkGraph:
    """Directed registry of control links with deterministic shortest paths."""

    def __init__(self) -> None:
        self._links: dict[tuple[str, int, str, int], ControlLink] = {}
        self._by_id: dict[str, ControlLink] = {}

    def add(self, link: ControlLink) -> None:
        if link.id in self._by_id:
            raise RegistrationError(f"link id {link.id!r} is already registered", code="duplicate_link")
        existing = self._links.get(link.key)
        if existing is not None:
            raise RegistrationError(
                f"a link from {link.source_id}@{link.source_version} to "
                f"{link.target_id}@{link.target_version} is already registered as {existing.id!r}",
                code="duplicate_link",
            )
        self._links[link.key] = link
        self._by_id[link.id] = link

    def links(self) -> tuple[ControlLink, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def get(self, link_id: str) -> ControlLink:
        try:
            return self._by_id[link_id]
        except KeyError as exc:
            raise RegistrationError(f"unknown control link {link_id!r}", code="unknown_link") from exc

    def replace(self, old_id: str, new_link: ControlLink) -> None:
        old = self.get(old_id)
        if new_link.id in self._by_id and new_link.id != old_id:
            raise RegistrationError(f"link id {new_link.id!r} is already registered", code="duplicate_link")
        del self._by_id[old_id]
        del self._links[old.key]
        self.add(new_link)

    def find_path(self, source: tuple[str, int], target: tuple[str, int]) -> LinkPath:
        if source == target:
            return LinkPath(source, target, (), Similarity.identity())
        adjacency: dict[tuple[str, int], list[LinkStep]] = {}
        for link in self._by_id.values():
            start = (link.source_id, link.source_version)
            end = (link.target_id, link.target_version)
            adjacency.setdefault(start, []).append(LinkStep(link, True))
            adjacency.setdefault(end, []).append(LinkStep(link, False))
        for steps in adjacency.values():
            steps.sort(key=lambda step: (step.link.id, step.forward))

        queue: deque[tuple[tuple[str, int], tuple[LinkStep, ...]]] = deque([(source, ())])
        visited = {source}
        while queue:
            node, steps = queue.popleft()
            for step in adjacency.get(node, ()):  # deterministic and cycle-free
                link = step.link
                next_node = (
                    (link.target_id, link.target_version)
                    if step.forward
                    else (link.source_id, link.source_version)
                )
                if next_node in visited:
                    continue
                extended = steps + (step,)
                if next_node == target:
                    transform = Similarity.identity()
                    for item in extended:
                        transform = compose(item.similarity, transform)
                    return LinkPath(source, target, extended, transform)
                visited.add(next_node)
                queue.append((next_node, extended))
        raise RegistrationError(
            f"no registered control link path from {source[0]}@{source[1]} to {target[0]}@{target[1]}",
            code="unlinked_network",
            details={"source": list(source), "target": list(target)},
        )


def compose(outer: Similarity, inner: Similarity) -> Similarity:
    """Return the similarity ``outer(inner(point))``."""
    from math import cos, sin

    angle = outer.rotation_rad + inner.rotation_rad
    scale = outer.scale * inner.scale
    rotated = Vec2(
        outer.scale * cos(outer.rotation_rad) * inner.translation.x
        - outer.scale * sin(outer.rotation_rad) * inner.translation.y,
        outer.scale * sin(outer.rotation_rad) * inner.translation.x
        + outer.scale * cos(outer.rotation_rad) * inner.translation.y,
    )
    return Similarity(scale, angle, rotated + outer.translation)
