from __future__ import annotations

import math
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
class PageSetupSummary:
    name: str
    model_type: bool
    plotter: str | None = None
    media_name: str | None = None
    plot_style: str | None = None
    use_standard_scale: bool | None = None
    standard_scale: int | None = None
    custom_scale_numerator: float | None = None
    custom_scale_denominator: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def has_verified_one_to_one_scale(self) -> bool:
        if self.use_standard_scale is True:
            # AutoCAD ActiveX acPlotScale.ac1_1 is stable at enum value 16.
            return self.standard_scale == 16
        if self.use_standard_scale is not False:
            return False
        numerator = self.custom_scale_numerator
        denominator = self.custom_scale_denominator
        return (
            numerator is not None
            and denominator is not None
            and math.isfinite(numerator)
            and math.isfinite(denominator)
            and numerator > 0
            and denominator > 0
            and math.isclose(numerator, denominator, rel_tol=1e-9, abs_tol=1e-12)
        )


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
    page_setups: list[PageSetupSummary] = field(default_factory=list)
    frames: list[FrameCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "layouts": [layout.to_dict() for layout in self.layouts],
            "page_setups": [page_setup.to_dict() for page_setup in self.page_setups],
            "frames": [frame.to_dict() for frame in self.frames],
            "warnings": list(self.warnings),
        }
