from pathlib import Path

import pytest

from cadplot_mcp.config import load_config


def test_config_matches_reversed_paper_dimensions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
version: 1
allowed_roots: [project]
paper_profiles:
  - id: sheet_70x100
    labels: [70x100]
    page_setup: OFFICE_70x100
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    tolerance_mm: 3
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    profile = config.match_paper_profile("1000 x 700 mm")

    assert profile is not None
    assert profile.id == "sheet_70x100"
    assert config.path_policy.allowed_roots == (project.resolve(),)


def test_config_rejects_overlapping_profile_dimensions(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
version: 1
allowed_roots: [project]
paper_profiles:
  - id: first
    labels: [70x100]
    page_setup: FIRST
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
  - id: second
    labels: [700x1000 mm]
    page_setup: SECOND
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dimensions overlap"):
        load_config(config_path)
