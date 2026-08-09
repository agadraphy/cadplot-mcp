from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from cadplot_mcp.paper import normalize_label, parse_paper_size
from cadplot_mcp.security import PathPolicy

MAX_CONFIG_BYTES = 1024 * 1024

TOP_LEVEL_KEYS = {
    "version",
    "allowed_roots",
    "template_roots",
    "workspace_root",
    "drawing_unit_mm",
    "scale_denominators",
    "scale_tolerance_ratio",
    "require_page_setup_match",
    "layout_prefix",
    "pdf_page_tolerance_mm",
    "minimum_frame_confidence",
    "inspection_timeout_seconds",
    "frame_layers",
    "paper_profiles",
}
PROFILE_KEYS = {
    "id",
    "labels",
    "page_setup",
    "plotter",
    "plot_style",
    "canonical_media",
    "template_layout",
    "template_drawing",
    "template_sha256",
    "tolerance_mm",
}


@dataclass(frozen=True, slots=True)
class PaperProfile:
    id: str
    labels: tuple[str, ...]
    page_setup: str
    plotter: str
    plot_style: str
    canonical_media: str | None = None
    template_layout: str | None = None
    template_drawing: Path | None = None
    template_sha256: str | None = None
    tolerance_mm: float = 3.0


@dataclass(frozen=True, slots=True)
class CadPlotConfig:
    source: Path
    path_policy: PathPolicy
    template_path_policy: PathPolicy | None
    paper_profiles: tuple[PaperProfile, ...]
    workspace_root: Path | None = None
    drawing_unit_mm: float = 1.0
    scale_denominators: tuple[float, ...] = (1, 2, 5, 10, 20, 25, 50, 100, 200, 500)
    scale_tolerance_ratio: float = 0.02
    require_page_setup_match: bool = True
    layout_prefix: str = "CADPLOT"
    pdf_page_tolerance_mm: float = 2.0
    minimum_frame_confidence: float = 0.85
    inspection_timeout_seconds: int = 120
    frame_layers: tuple[str, ...] = ()

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
    if not source.is_file():
        raise ValueError("Config path must be a file.")
    if source.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError("Config exceeds the 1 MiB safety limit.")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError("Config must be valid UTF-8 YAML.") from exc
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a YAML mapping.")
    unknown_keys = sorted(set(raw) - TOP_LEVEL_KEYS)
    if unknown_keys:
        raise ValueError(f"Config contains unknown fields: {', '.join(unknown_keys)}")
    if raw.get("version") != 1:
        raise ValueError("Unsupported or missing config version; expected version: 1")

    roots_value = raw.get("allowed_roots", [])
    if not isinstance(roots_value, list) or any(
        not isinstance(item, str) or not item.strip() for item in roots_value
    ):
        raise ValueError("allowed_roots must be a list of non-empty paths.")
    template_roots_value = raw.get("template_roots", [])
    if not isinstance(template_roots_value, list) or any(
        not isinstance(item, str) or not item.strip() for item in template_roots_value
    ):
        raise ValueError("template_roots must be a list of non-empty paths.")
    profiles_value = raw.get("paper_profiles", [])
    if not isinstance(profiles_value, list):
        raise ValueError("paper_profiles must be a list of profile mappings.")
    roots = [_resolve_relative(source.parent, item) for item in roots_value]
    template_roots = [
        _resolve_relative(source.parent, item) for item in template_roots_value
    ]
    profiles = tuple(_parse_profile(item, source.parent) for item in profiles_value)
    workspace_value = raw.get("workspace_root")
    if workspace_value is not None and (
        not isinstance(workspace_value, str) or not workspace_value.strip()
    ):
        raise ValueError("workspace_root must be a non-empty path when configured.")
    workspace_root = (
        _resolve_relative(source.parent, workspace_value).absolute()
        if workspace_value is not None
        else None
    )
    drawing_unit_mm = float(raw.get("drawing_unit_mm", 1.0))
    default_scales = (1, 2, 5, 10, 20, 25, 50, 100, 200, 500)
    scale_values = raw.get("scale_denominators", default_scales)
    if not isinstance(scale_values, (list, tuple)):
        raise ValueError("scale_denominators must be a list of numbers.")
    scale_denominators = tuple(float(item) for item in scale_values)
    scale_tolerance_ratio = float(raw.get("scale_tolerance_ratio", 0.02))
    require_page_setup_match = raw.get("require_page_setup_match", True)
    layout_prefix = str(raw.get("layout_prefix", "CADPLOT"))
    pdf_page_tolerance_mm = float(raw.get("pdf_page_tolerance_mm", 2.0))
    minimum_frame_confidence = float(raw.get("minimum_frame_confidence", 0.85))
    inspection_timeout_seconds = raw.get("inspection_timeout_seconds", 120)
    frame_layer_values = raw.get("frame_layers", [])
    if not isinstance(frame_layer_values, list) or any(
        not isinstance(item, str) for item in frame_layer_values
    ):
        raise ValueError("frame_layers must be a list of layer names.")
    frame_layers = tuple(item.strip() for item in frame_layer_values)
    if not math.isfinite(drawing_unit_mm) or drawing_unit_mm <= 0:
        raise ValueError("drawing_unit_mm must be a finite value greater than zero")
    if not 1 <= len(scale_denominators) <= 100 or any(
        not math.isfinite(item) or item <= 0 for item in scale_denominators
    ):
        raise ValueError("scale_denominators must contain positive values")
    if len(scale_denominators) != len(set(scale_denominators)):
        raise ValueError("scale_denominators must be unique")
    if not 0 <= scale_tolerance_ratio <= 0.1:
        raise ValueError("scale_tolerance_ratio must be between 0 and 0.1")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", layout_prefix):
        raise ValueError("layout_prefix must contain 1-32 letters, digits, underscores, or hyphens")
    if not isinstance(require_page_setup_match, bool):
        raise ValueError("require_page_setup_match must be true or false")
    if not 0 <= pdf_page_tolerance_mm <= 10:
        raise ValueError("pdf_page_tolerance_mm must be between 0 and 10")
    if not 0 <= minimum_frame_confidence <= 1:
        raise ValueError("minimum_frame_confidence must be between 0 and 1")
    if (
        not isinstance(inspection_timeout_seconds, int)
        or isinstance(inspection_timeout_seconds, bool)
        or not 5 <= inspection_timeout_seconds <= 600
    ):
        raise ValueError("inspection_timeout_seconds must be an integer between 5 and 600")
    if len(frame_layers) > 256 or any(
        not item or len(item) > 255 or _has_control_character(item) for item in frame_layers
    ):
        raise ValueError("frame_layers must contain at most 256 valid, non-empty names")
    normalized_layers = [item.casefold() for item in frame_layers]
    if len(normalized_layers) != len(set(normalized_layers)):
        raise ValueError("frame_layers must be unique ignoring case")
    _validate_profiles(profiles)
    path_policy = PathPolicy.from_roots(roots)
    template_path_policy = (
        PathPolicy.from_roots(template_roots) if template_roots else None
    )
    resolved_profiles: list[PaperProfile] = []
    for profile in profiles:
        if profile.template_drawing is None:
            resolved_profiles.append(profile)
            continue
        if template_path_policy is None:
            raise ValueError(
                f"Paper profile {profile.id} requires template_roots for template_drawing"
            )
        template = template_path_policy.require_allowed(profile.template_drawing)
        if template.suffix.casefold() not in {".dwg", ".dwt"} or not template.is_file():
            raise ValueError(
                f"Paper profile {profile.id} template_drawing must be a DWG or DWT file"
            )
        resolved_profiles.append(replace(profile, template_drawing=template))
    profiles = tuple(resolved_profiles)
    if workspace_root is not None:
        resolved_workspace = workspace_root.resolve(strict=False)
        read_roots = list(path_policy.allowed_roots)
        if template_path_policy is not None:
            read_roots.extend(template_path_policy.allowed_roots)
        if any(
            resolved_workspace == root
            or root in resolved_workspace.parents
            or resolved_workspace in root.parents
            for root in read_roots
        ):
            raise ValueError(
                "workspace_root must be separate from allowed_roots and template_roots"
            )
    return CadPlotConfig(
        source=source,
        path_policy=path_policy,
        template_path_policy=template_path_policy,
        paper_profiles=profiles,
        workspace_root=workspace_root,
        drawing_unit_mm=drawing_unit_mm,
        scale_denominators=scale_denominators,
        scale_tolerance_ratio=scale_tolerance_ratio,
        require_page_setup_match=require_page_setup_match,
        layout_prefix=layout_prefix,
        pdf_page_tolerance_mm=pdf_page_tolerance_mm,
        minimum_frame_confidence=minimum_frame_confidence,
        inspection_timeout_seconds=inspection_timeout_seconds,
        frame_layers=frame_layers,
    )


def _resolve_relative(base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else base / candidate


def _parse_profile(raw: dict[str, Any], base: Path) -> PaperProfile:
    if not isinstance(raw, dict):
        raise ValueError("Each paper profile must be a mapping.")
    unknown_keys = sorted(set(raw) - PROFILE_KEYS)
    if unknown_keys:
        raise ValueError(f"Paper profile contains unknown fields: {', '.join(unknown_keys)}")
    required = ("id", "labels", "page_setup", "plotter", "plot_style")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"Paper profile is missing fields: {', '.join(missing)}")
    labels_value = raw["labels"]
    if not isinstance(labels_value, list) or any(
        not isinstance(item, str) for item in labels_value
    ):
        raise ValueError(f"Paper profile {raw['id']} labels must be a list of strings")
    labels = tuple(item.strip() for item in labels_value)
    if not labels or any(
        not item or len(item) > 128 or _has_control_character(item) for item in labels
    ):
        raise ValueError(f"Paper profile {raw['id']} must define at least one label")
    profile_id = str(raw["id"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id):
        raise ValueError("Paper profile id must contain 1-64 safe ASCII characters")
    page_setup = _profile_text(raw, "page_setup", 255)
    plotter = _profile_text(raw, "plotter", 255)
    plot_style = _profile_text(raw, "plot_style", 255)
    canonical_media = (
        _profile_text(raw, "canonical_media", 512)
        if raw.get("canonical_media") is not None
        else None
    )
    template_layout = (
        _profile_text(raw, "template_layout", 255)
        if raw.get("template_layout") is not None
        else None
    )
    template_drawing = (
        _resolve_relative(base, _profile_text(raw, "template_drawing", 4_096))
        if raw.get("template_drawing") is not None
        else None
    )
    template_sha256 = (
        _profile_text(raw, "template_sha256", 64).lower()
        if raw.get("template_sha256") is not None
        else None
    )
    return PaperProfile(
        id=profile_id,
        labels=labels,
        page_setup=page_setup,
        plotter=plotter,
        plot_style=plot_style,
        canonical_media=canonical_media,
        template_layout=template_layout,
        template_drawing=template_drawing,
        template_sha256=template_sha256,
        tolerance_mm=float(raw.get("tolerance_mm", 3.0)),
    )


def _validate_profiles(profiles: tuple[PaperProfile, ...]) -> None:
    if not 1 <= len(profiles) <= 100:
        raise ValueError("paper_profiles must contain between 1 and 100 profiles")
    ids = [profile.id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("Paper profile ids must be unique")

    aliases: dict[str, str] = {}
    for profile in profiles:
        if not math.isfinite(profile.tolerance_mm) or not 0 <= profile.tolerance_mm <= 50:
            raise ValueError(
                f"Paper profile {profile.id} tolerance must be finite and between 0 and 50"
            )
        if profile.canonical_media is not None and not profile.canonical_media.strip():
            raise ValueError(f"Paper profile {profile.id} has an empty canonical_media")
        if profile.template_drawing is not None and profile.template_layout is None:
            raise ValueError(
                f"Paper profile {profile.id} template_drawing requires template_layout"
            )
        if (profile.template_drawing is None) != (profile.template_sha256 is None):
            raise ValueError(
                f"Paper profile {profile.id} template_drawing and template_sha256 must be paired"
            )
        if profile.template_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", profile.template_sha256
        ):
            raise ValueError(f"Paper profile {profile.id} has an invalid template_sha256")
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
                raise ValueError(f"Paper profile dimensions overlap: {left.id!r} and {right.id!r}")


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


def _profile_text(raw: dict[str, Any], key: str, max_length: int) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise ValueError(f"Paper profile {key} must be a string")
    value = value.strip()
    if not value or len(value) > max_length or _has_control_character(value):
        raise ValueError(f"Paper profile {key} must be a valid non-empty value")
    return value


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
