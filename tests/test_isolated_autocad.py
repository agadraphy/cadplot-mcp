import json
import subprocess
import sys
from pathlib import Path

import pytest

from cadplot_mcp.backends.isolated_autocad import (
    AutoCADInspectionTimeoutError,
    AutoCADInspectorProtocolError,
    IsolatedAutoCADInspector,
)
from cadplot_mcp.inspector_worker import _handle_request
from cadplot_mcp.models import DrawingInspection
from cadplot_mcp.security import PathPolicy


def _inspection_payload(drawing: Path) -> dict[str, object]:
    return {
        "path": str(drawing.resolve()),
        "layouts": [
            {
                "name": "Layout1",
                "model_type": False,
                "plotter": "DWG To PDF.pc3",
                "media_name": "ISO_A4",
                "plot_style": "monochrome.ctb",
                "plot_type": 5,
                "use_standard_scale": True,
                "standard_scale": 16,
            }
        ],
        "page_setups": [
            {
                "name": "OFFICE_A4",
                "model_type": False,
                "plotter": "DWG To PDF.pc3",
                "media_name": "ISO_A4",
                "plot_style": "monochrome.ctb",
                "plot_type": 5,
                "use_standard_scale": True,
                "standard_scale": 16,
                "custom_scale_numerator": 1.0,
                "custom_scale_denominator": 1.0,
            }
        ],
        "frames": [
            {
                "handle": "A1",
                "layer": "SHEET",
                "min_point": [0.0, 0.0, 0.0],
                "max_point": [297.0, 210.0, 0.0],
                "label": "A4",
                "width_mm": 210.0,
                "height_mm": 297.0,
                "confidence": 0.9,
            }
        ],
        "warnings": [],
    }


def test_isolated_inspector_uses_stdin_protocol_and_rebuilds_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = tmp_path / "sheet.dwg"
    drawing.write_bytes(b"dwg")
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        stdout = json.dumps(
            {"ok": True, "inspection": _inspection_payload(drawing)}
        ).encode("utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr("cadplot_mcp.backends.isolated_autocad.subprocess.run", fake_run)
    inspector = IsolatedAutoCADInspector(PathPolicy.from_roots([tmp_path]), timeout_seconds=30)

    inspection = inspector.inspect_drawing(drawing)

    assert inspection.path == str(drawing.resolve())
    assert inspection.page_setups[0].plot_type == 5
    assert inspection.frames[0].min_point == (0.0, 0.0, 0.0)
    assert captured["command"][1:] == ["-m", "cadplot_mcp.inspector_worker"]
    assert str(drawing) not in captured["command"]
    request = json.loads(captured["input"].decode("utf-8"))
    assert request["drawing"] == str(drawing.resolve())
    assert captured["timeout"] == 30


def test_isolated_inspector_terminates_and_bounds_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = tmp_path / "blocked.dwg"
    drawing.write_bytes(b"dwg")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("cadplot_mcp.backends.isolated_autocad.subprocess.run", fake_run)
    inspector = IsolatedAutoCADInspector(PathPolicy.from_roots([tmp_path]), timeout_seconds=5)

    with pytest.raises(AutoCADInspectionTimeoutError, match="exceeded 5 seconds"):
        inspector.inspect_drawing(drawing)


def test_isolated_inspector_rejects_path_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = tmp_path / "sheet.dwg"
    other = tmp_path / "other.dwg"
    drawing.write_bytes(b"dwg")
    other.write_bytes(b"dwg")

    def fake_run(command, **kwargs):
        payload = _inspection_payload(other)
        stdout = json.dumps({"ok": True, "inspection": payload}).encode("utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr("cadplot_mcp.backends.isolated_autocad.subprocess.run", fake_run)
    inspector = IsolatedAutoCADInspector(PathPolicy.from_roots([tmp_path]), timeout_seconds=30)

    with pytest.raises(AutoCADInspectorProtocolError, match="does not match request"):
        inspector.inspect_drawing(drawing)


def test_isolated_inspector_rejects_unknown_nested_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = tmp_path / "sheet.dwg"
    drawing.write_bytes(b"dwg")

    def fake_run(command, **kwargs):
        payload = _inspection_payload(drawing)
        payload["frames"][0]["unexpected"] = "field"
        stdout = json.dumps({"ok": True, "inspection": payload}).encode("utf-8")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr("cadplot_mcp.backends.isolated_autocad.subprocess.run", fake_run)
    inspector = IsolatedAutoCADInspector(PathPolicy.from_roots([tmp_path]), timeout_seconds=30)

    with pytest.raises(AutoCADInspectorProtocolError, match="invalid frame fields"):
        inspector.inspect_drawing(drawing)


def test_worker_request_schema_is_closed_and_returns_structured_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = tmp_path / "sheet.dwg"
    drawing.write_bytes(b"dwg")
    monkeypatch.setattr(
        "cadplot_mcp.inspector_worker.AutoCADComInspector.inspect_drawing",
        lambda self, value: DrawingInspection(path=str(Path(value).resolve())),
    )
    request = {
        "schema_version": 1,
        "drawing": str(drawing),
        "allowed_roots": [str(tmp_path)],
    }

    response = _handle_request(request)
    rejected = _handle_request({**request, "unexpected": True})

    assert response == {
        "ok": True,
        "inspection": {
            "path": str(drawing.resolve()),
            "layouts": [],
            "page_setups": [],
            "frames": [],
            "warnings": [],
        },
    }
    assert rejected == {"ok": False, "error": "Inspection request schema is invalid."}


def test_worker_module_rejects_invalid_subprocess_request_without_contacting_autocad() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cadplot_mcp.inspector_worker"],
        input=b"{}",
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stderr == b""
    assert json.loads(result.stdout) == {
        "ok": False,
        "error": "Inspection request schema is invalid.",
    }
