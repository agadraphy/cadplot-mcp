from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any, TypeVar

from cadplot_mcp.backends.autocad_com import AutoCADUnavailableError
from cadplot_mcp.models import (
    DrawingInspection,
    FrameCandidate,
    LayoutSummary,
    PageSetupSummary,
)
from cadplot_mcp.security import PathPolicy

MAX_WORKER_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_LAYOUTS = 5_000
MAX_PAGE_SETUPS = 1_000
MAX_FRAMES = 5_000
MAX_WARNINGS = 5_000
MAX_TEXT_LENGTH = 4_096


class AutoCADInspectionTimeoutError(AutoCADUnavailableError):
    """Raised after terminating an isolated inspection that exceeded its deadline."""


class AutoCADInspectorProtocolError(AutoCADUnavailableError):
    """Raised when the isolated helper returns malformed or excessive output."""


class IsolatedAutoCADInspector:
    """Run risky DWG COM inspection in a killable child process."""

    def __init__(self, path_policy: PathPolicy, *, timeout_seconds: int = 120) -> None:
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or not 5 <= timeout_seconds <= 600
        ):
            raise ValueError("Inspection timeout must be an integer between 5 and 600 seconds.")
        self.path_policy = path_policy
        self.timeout_seconds = timeout_seconds

    def inspect_drawing(self, value: str | Path) -> DrawingInspection:
        drawing_path = self.path_policy.require_allowed(value, suffix=".dwg")
        request = {
            "schema_version": 1,
            "drawing": str(drawing_path),
            "allowed_roots": [str(root) for root in self.path_policy.allowed_roots],
        }
        response = _run_worker(request, timeout_seconds=self.timeout_seconds)
        if response.get("ok") is not True:
            raise AutoCADUnavailableError(_bounded_error(response.get("error")))
        return _inspection_from_payload(response.get("inspection"), drawing_path)


def _run_worker(request: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [sys.executable, "-m", "cadplot_mcp.inspector_worker"],
            input=encoded,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        raise AutoCADInspectionTimeoutError(
            f"AutoCAD inspection exceeded {timeout_seconds} seconds and its helper process was "
            "terminated. Close modal dialogs and inspect this drawing manually before retrying."
        ) from exc
    except OSError as exc:
        raise AutoCADUnavailableError("Could not start the isolated AutoCAD inspector.") from exc

    if len(result.stdout) > MAX_WORKER_OUTPUT_BYTES:
        raise AutoCADInspectorProtocolError("AutoCAD inspector output exceeded the 8 MiB limit.")
    try:
        response = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutoCADInspectorProtocolError(
            "AutoCAD inspector returned invalid UTF-8 JSON."
        ) from exc
    if not isinstance(response, dict) or set(response) not in (
        {"ok", "inspection"},
        {"ok", "error"},
    ):
        raise AutoCADInspectorProtocolError(
            "AutoCAD inspector returned an invalid response schema."
        )
    if result.returncode != 0:
        raise AutoCADInspectorProtocolError("AutoCAD inspector process exited unexpectedly.")
    if response["ok"] is True and set(response) != {"ok", "inspection"}:
        raise AutoCADInspectorProtocolError("AutoCAD inspector success response is malformed.")
    if response["ok"] is False and set(response) != {"ok", "error"}:
        raise AutoCADInspectorProtocolError("AutoCAD inspector error response is malformed.")
    if not isinstance(response["ok"], bool):
        raise AutoCADInspectorProtocolError("AutoCAD inspector response has an invalid ok flag.")
    return response


def _inspection_from_payload(value: Any, expected_path: Path) -> DrawingInspection:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "layouts",
        "page_setups",
        "frames",
        "warnings",
    }:
        raise AutoCADInspectorProtocolError("AutoCAD inspector returned invalid inspection fields.")
    path_text = _text(value["path"], "inspection path")
    try:
        reported_path = Path(path_text).expanduser().resolve(strict=True)
    except OSError as exc:
        raise AutoCADInspectorProtocolError(
            "AutoCAD inspector returned an unavailable drawing path."
        ) from exc
    if os.path.normcase(str(reported_path)) != os.path.normcase(str(expected_path)):
        raise AutoCADInspectorProtocolError(
            "AutoCAD inspector drawing path does not match request."
        )

    layouts = [
        _layout(item) for item in _bounded_list(value["layouts"], "layouts", MAX_LAYOUTS)
    ]
    page_setups = [
        _page_setup(item)
        for item in _bounded_list(value["page_setups"], "page setups", MAX_PAGE_SETUPS)
    ]
    frames = [
        _frame(item) for item in _bounded_list(value["frames"], "frames", MAX_FRAMES)
    ]
    warnings = [
        _warning_text(item)
        for item in _bounded_list(value["warnings"], "warnings", MAX_WARNINGS)
    ]
    return DrawingInspection(
        path=str(reported_path),
        layouts=layouts,
        page_setups=page_setups,
        frames=frames,
        warnings=warnings,
    )


def _layout(value: Any) -> LayoutSummary:
    record = _record(value, LayoutSummary, "layout")
    return LayoutSummary(
        name=_text(record["name"], "layout name"),
        model_type=_bool(record["model_type"], "layout model_type"),
        plotter=_optional_text(record["plotter"], "layout plotter"),
        media_name=_optional_text(record["media_name"], "layout media"),
        plot_style=_optional_text(record["plot_style"], "layout plot style"),
        plot_type=_optional_int(record["plot_type"], "layout plot type"),
        use_standard_scale=_optional_bool(
            record["use_standard_scale"], "layout standard-scale flag"
        ),
        standard_scale=_optional_int(record["standard_scale"], "layout standard scale"),
    )


def _page_setup(value: Any) -> PageSetupSummary:
    record = _record(value, PageSetupSummary, "page setup")
    return PageSetupSummary(
        name=_text(record["name"], "page setup name"),
        model_type=_bool(record["model_type"], "page setup model_type"),
        plotter=_optional_text(record["plotter"], "page setup plotter"),
        media_name=_optional_text(record["media_name"], "page setup media"),
        plot_style=_optional_text(record["plot_style"], "page setup plot style"),
        plot_type=_optional_int(record["plot_type"], "page setup plot type"),
        use_standard_scale=_optional_bool(
            record["use_standard_scale"], "page setup standard-scale flag"
        ),
        standard_scale=_optional_int(record["standard_scale"], "page setup standard scale"),
        custom_scale_numerator=_optional_number(
            record["custom_scale_numerator"], "page setup custom-scale numerator"
        ),
        custom_scale_denominator=_optional_number(
            record["custom_scale_denominator"], "page setup custom-scale denominator"
        ),
    )


def _frame(value: Any) -> FrameCandidate:
    record = _record(value, FrameCandidate, "frame")
    width = _number(record["width_mm"], "frame paper width")
    height = _number(record["height_mm"], "frame paper height")
    confidence = _number(record["confidence"], "frame confidence")
    if width <= 0 or height <= 0 or not 0 <= confidence <= 1:
        raise AutoCADInspectorProtocolError("AutoCAD inspector returned invalid frame metrics.")
    return FrameCandidate(
        handle=_text(record["handle"], "frame handle"),
        layer=_text(record["layer"], "frame layer"),
        min_point=_point(record["min_point"], "frame minimum"),
        max_point=_point(record["max_point"], "frame maximum"),
        label=_text(record["label"], "frame label"),
        width_mm=width,
        height_mm=height,
        confidence=confidence,
    )


T = TypeVar("T")


def _record(value: Any, model: type[T], name: str) -> dict[str, Any]:
    expected = {field.name for field in fields(model)}
    if not isinstance(value, dict) or set(value) != expected:
        raise AutoCADInspectorProtocolError(
            f"AutoCAD inspector returned invalid {name} fields."
        )
    return value


def _bounded_list(value: Any, name: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise AutoCADInspectorProtocolError(
            f"AutoCAD inspector returned an invalid or excessive {name} collection."
        )
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH or _has_control(value):
        raise AutoCADInspectorProtocolError(f"AutoCAD inspector returned invalid {name}.")
    return value


def _optional_text(value: Any, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise AutoCADInspectorProtocolError(f"AutoCAD inspector returned invalid {name}.")
    return value


def _optional_bool(value: Any, name: str) -> bool | None:
    return None if value is None else _bool(value, name)


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise AutoCADInspectorProtocolError(f"AutoCAD inspector returned invalid {name}.")
    return value


def _number(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AutoCADInspectorProtocolError(f"AutoCAD inspector returned invalid {name}.")
    result = float(value)
    if not math.isfinite(result):
        raise AutoCADInspectorProtocolError(f"AutoCAD inspector returned invalid {name}.")
    return result


def _optional_number(value: Any, name: str) -> float | None:
    return None if value is None else _number(value, name)


def _point(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise AutoCADInspectorProtocolError(f"AutoCAD inspector returned invalid {name} point.")
    items = tuple(_number(item, name) for item in value)
    return items[0], items[1], items[2]


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _bounded_error(value: Any) -> str:
    if not isinstance(value, str):
        return "AutoCAD inspection failed without a valid error message."
    cleaned = " ".join(value.split())
    return cleaned[:1_000] or "AutoCAD inspection failed without a valid error message."


def _warning_text(value: Any) -> str:
    if not isinstance(value, str):
        raise AutoCADInspectorProtocolError("AutoCAD inspector returned an invalid warning.")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > MAX_TEXT_LENGTH:
        raise AutoCADInspectorProtocolError("AutoCAD inspector returned an invalid warning.")
    return cleaned
