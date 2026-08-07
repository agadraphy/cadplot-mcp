from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DrawingFile:
    path: str
    size_bytes: int
    modified_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LayoutSummary:
    name: str
    model_type: bool
    plotter: str | None = None
    media_name: str | None = None
    plot_style: str | None = None
    plot_type: int | None = None
    use_standard_scale: bool | None = None
    standard_scale: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FrameCandidate:
    handle: str
    layer: str
    min_point: tuple[float, float, float]
    max_point: tuple[float, float, float]
    label: str
    width_mm: float
    height_mm: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DrawingInspection:
    path: str
    layouts: list[LayoutSummary] = field(default_factory=list)
    frames: list[FrameCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "layouts": [layout.to_dict() for layout in self.layouts],
            "frames": [frame.to_dict() for frame in self.frames],
            "warnings": list(self.warnings),
        }

