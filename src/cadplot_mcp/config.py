from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cadplot_mcp.paper import normalize_label, parse_paper_size
from cadplot_mcp.security import PathPolicy


@dataclass(frozen=True, slots=True)
class PaperProfile:
    id: str
    labels: tuple[str, ...]
    page_setup: str
    plotter: str
    plot_style: str
    tolerance_mm: float = 3.0


@dataclass(frozen=True, slots=True)
class CadPlotConfig:
    source: Path
    path_policy: PathPolicy
    paper_profiles: tuple[PaperProfile, ...]
    workspace_root: Path | None = None
    drawing_unit_mm: float = 1.0
    scale_denominators: tuple[float, ...] = (1, 2, 5, 10, 20, 25, 50, 100, 200, 500)
    scale_tolerance_ratio: float = 0.02

    def match_paper_profile(self, label: str) -> PaperProfile | None:
        normalized = normalize_label(label)
        direct_matches = [
            profile
            for profile in self.paper_profiles
            if normalized in {normalize_label(item) for item in profile.labels}
        ]
        if len(direct_matches) > 1:
            raise ValueError(f"Ambiguous paper profile for label: {label}")
        if direct_matches:
            return direct_matches[0]

        requested = parse_paper_size(label)
        if not requested:
            return None
        req_w, req_h = requested.orientation_independent
        size_matches: list[PaperProfile] = []
        for profile in self.paper_profiles:
            for alias in profile.labels:
                size = parse_paper_size(alias)
                if not size:
                    continue
                width, height = size.orientation_independent
                if (
                    abs(width - req_w) <= profile.tolerance_mm
                    and abs(height - req_h) <= profile.tolerance_mm
                ):
                    size_matches.append(profile)
                    break
        if len(size_matches) > 1:
            raise ValueError(f"Ambiguous paper profile for dimensions in label: {label}")
        return size_matches[0] if size_matches else None


def load_config(path: str | Path) -> CadPlotConfig:
    source = Path(path).expanduser().resolve(strict=True)
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if raw.get("version") != 1:
        raise ValueError("Unsupported or missing config version; expected version: 1")

    roots = [_resolve_relative(source.parent, item) for item in raw.get("allowed_roots", [])]
    profiles = tuple(_parse_profile(item) for item in raw.get("paper_profiles", []))
    workspace_value = raw.get("workspace_root")
    workspace_root = (
        _resolve_relative(source.parent, str(workspace_value)).absolute()
        if workspace_value is not None
        else None
    )
    drawing_unit_mm = float(raw.get("drawing_unit_mm", 1.0))
    default_scales = (1, 2, 5, 10, 20, 25, 50, 100, 200, 500)
    scale_denominators = tuple(
        float(item) for item in raw.get("scale_denominators", default_scales)
    )
    scale_tolerance_ratio = float(raw.get("scale_tolerance_ratio", 0.02))
    if drawing_unit_mm <= 0:
        raise ValueError("drawing_unit_mm must be greater than zero")
    if not scale_denominators or any(item <= 0 for item in scale_denominators):
        raise ValueError("scale_denominators must contain positive values")
    if len(scale_denominators) != len(set(scale_denominators)):
        raise ValueError("scale_denominators must be unique")
    if not 0 <= scale_tolerance_ratio <= 0.1:
        raise ValueError("scale_tolerance_ratio must be between 0 and 0.1")
    _validate_profiles(profiles)
    return CadPlotConfig(
        source=source,
        path_policy=PathPolicy.from_roots(roots),
        paper_profiles=profiles,
        workspace_root=workspace_root,
        drawing_unit_mm=drawing_unit_mm,
        scale_denominators=scale_denominators,
        scale_tolerance_ratio=scale_tolerance_ratio,
    )


def _resolve_relative(base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else base / candidate


def _parse_profile(raw: dict[str, Any]) -> PaperProfile:
    required = ("id", "labels", "page_setup", "plotter", "plot_style")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"Paper profile is missing fields: {', '.join(missing)}")
    labels = tuple(str(item) for item in raw["labels"])
    if not labels:
        raise ValueError(f"Paper profile {raw['id']} must define at least one label")
    return PaperProfile(
        id=str(raw["id"]),
        labels=labels,
        page_setup=str(raw["page_setup"]),
        plotter=str(raw["plotter"]),
        plot_style=str(raw["plot_style"]),
        tolerance_mm=float(raw.get("tolerance_mm", 3.0)),
    )


def _validate_profiles(profiles: tuple[PaperProfile, ...]) -> None:
    ids = [profile.id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("Paper profile ids must be unique")

    aliases: dict[str, str] = {}
    for profile in profiles:
        if profile.tolerance_mm < 0:
            raise ValueError(f"Paper profile {profile.id} has a negative tolerance")
        for label in profile.labels:
            normalized = normalize_label(label)
            owner = aliases.get(normalized)
            if owner and owner != profile.id:
                raise ValueError(
                    f"Paper label {label!r} is shared by profiles {owner!r} and {profile.id!r}"
                )
            aliases[normalized] = profile.id

    for index, left in enumerate(profiles):
        for right in profiles[index + 1 :]:
            if _profiles_overlap(left, right):
                raise ValueError(
                    f"Paper profile dimensions overlap: {left.id!r} and {right.id!r}"
                )


def _profiles_overlap(left: PaperProfile, right: PaperProfile) -> bool:
    left_sizes = [size for label in left.labels if (size := parse_paper_size(label))]
    right_sizes = [size for label in right.labels if (size := parse_paper_size(label))]
    for left_size in left_sizes:
        left_width, left_height = left_size.orientation_independent
        for right_size in right_sizes:
            right_width, right_height = right_size.orientation_independent
            if (
                abs(left_width - right_width) <= left.tolerance_mm + right.tolerance_mm
                and abs(left_height - right_height) <= left.tolerance_mm + right.tolerance_mm
            ):
                return True
    return False
