from pathlib import Path

import pytest

from cadplot_mcp.security import PathPolicy, PathPolicyError


def test_allowed_file_inside_root(tmp_path: Path) -> None:
    drawing = tmp_path / "project" / "sheet.dwg"
    drawing.parent.mkdir()
    drawing.touch()
    policy = PathPolicy.from_roots([drawing.parent])

    assert policy.require_allowed(drawing, suffix=".dwg") == drawing.resolve()


def test_rejects_file_outside_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.dwg"
    outside.touch()
    policy = PathPolicy.from_roots([allowed])

    with pytest.raises(PathPolicyError, match="outside configured allowed roots"):
        policy.require_allowed(outside)


def test_rejects_wrong_extension(tmp_path: Path) -> None:
    file = tmp_path / "notes.txt"
    file.touch()
    policy = PathPolicy.from_roots([tmp_path])

    with pytest.raises(PathPolicyError, match="Expected a .dwg file"):
        policy.require_allowed(file, suffix=".dwg")

