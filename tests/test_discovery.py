from pathlib import Path

import pytest

from cadplot_mcp.discovery import scan_drawings
from cadplot_mcp.security import PathPolicy


def test_scan_drawings_is_recursive_and_ignores_temp_files(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "A.dwg").write_bytes(b"a")
    (nested / "b.DWG").write_bytes(b"bb")
    (nested / "~temp.dwg").write_bytes(b"temp")
    (nested / "notes.txt").write_text("no", encoding="utf-8")
    policy = PathPolicy.from_roots([tmp_path])

    drawings = scan_drawings(tmp_path, policy)

    assert [Path(item.path).name for item in drawings] == ["A.dwg", "b.DWG"]
    assert [item.size_bytes for item in drawings] == [1, 2]


def test_scan_drawings_enforces_limit(tmp_path: Path) -> None:
    (tmp_path / "one.dwg").touch()
    (tmp_path / "two.dwg").touch()
    policy = PathPolicy.from_roots([tmp_path])

    with pytest.raises(ValueError, match="max_files is 1"):
        scan_drawings(tmp_path, policy, max_files=1)


def test_scan_drawings_rejects_symlink_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.dwg"
    outside.touch()
    link = allowed / "linked.dwg"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows installation")
    policy = PathPolicy.from_roots([allowed])

    with pytest.raises(ValueError, match="outside configured allowed roots"):
        scan_drawings(allowed, policy)
