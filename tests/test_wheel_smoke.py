import importlib.util
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "smoke-wheel-install.py"
SPEC = importlib.util.spec_from_file_location("cadplot_wheel_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_wheel_selector_requires_exactly_one_distribution(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        MODULE._select_wheel(tmp_path)

    (tmp_path / "cadplot_mcp-0.1.0-py3-none-any.whl").write_bytes(b"first")
    selected = MODULE._select_wheel(tmp_path)
    assert selected.name == "cadplot_mcp-0.1.0-py3-none-any.whl"

    (tmp_path / "cadplot_mcp-0.2.0-py3-none-any.whl").write_bytes(b"second")
    with pytest.raises(ValueError, match="found 2"):
        MODULE._select_wheel(tmp_path)


def test_wheel_selector_rejects_non_wheel_file(tmp_path: Path) -> None:
    archive = tmp_path / "cadplot_mcp-0.1.0.tar.gz"
    archive.write_bytes(b"source")

    with pytest.raises(ValueError, match=r"\.whl"):
        MODULE._select_wheel(archive)
