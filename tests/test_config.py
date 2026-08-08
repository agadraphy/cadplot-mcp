from pathlib import Path

import pytest

from cadplot_mcp.config import MAX_CONFIG_BYTES, load_config


def test_config_matches_reversed_paper_dimensions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
version: 1
allowed_roots: [project]
workspace_root: work
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
    assert config.workspace_root == (tmp_path / "work").absolute()
    assert config.drawing_unit_mm == 1
    assert 50 in config.scale_denominators


def test_config_accepts_optional_template_layout(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    path = tmp_path / "config.yaml"
    path.write_text(
        """
version: 1
allowed_roots: [project]
paper_profiles:
  - id: sheet
    labels: [70x100]
    page_setup: OFFICE
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    template_layout: TEMPLATE_70X100
""".strip(),
        encoding="utf-8",
    )

    assert load_config(path).paper_profiles[0].template_layout == "TEMPLATE_70X100"


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


@pytest.mark.parametrize(
    ("setting", "message"),
    [
        ("drawing_unit_mm: 0", "drawing_unit_mm"),
        ("scale_denominators: [1, 1]", "must be unique"),
        ("scale_denominators: [1, -50]", "positive values"),
        ("scale_tolerance_ratio: 0.5", "between 0 and 0.1"),
        ("layout_prefix: ../bad", "layout_prefix"),
        ("require_page_setup_match: maybe", "must be true or false"),
        ("pdf_page_tolerance_mm: 20", "between 0 and 10"),
        ("minimum_frame_confidence: 2", "between 0 and 1"),
        ("frame_layers: [SHEET, sheet]", "unique ignoring case"),
        ("drawing_unit_mm: .nan", "finite value"),
        ("scale_denominators: [1, .inf]", "positive values"),
        ("frame_layers: SHEET", "must be a list"),
    ],
)
def test_config_rejects_unsafe_scale_settings(tmp_path: Path, setting: str, message: str) -> None:
    (tmp_path / "project").mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
allowed_roots: [project]
{setting}
paper_profiles:
  - id: sheet
    labels: [70x100]
    page_setup: OFFICE
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_config(config_path)


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        (
            """id: bad/profile
    labels: [70x100]
    page_setup: OFFICE
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb""",
            "profile id",
        ),
        (
            """id: sheet
    labels: 70x100
    page_setup: OFFICE
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb""",
            "labels must be a list",
        ),
        (
            """id: sheet
    labels: [70x100]
    page_setup: ""
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb""",
            "page_setup",
        ),
        (
            """id: sheet
    labels: [70x100]
    page_setup: OFFICE
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    tolerance_mm: .nan""",
            "tolerance must be finite",
        ),
        (
            """id: sheet
    labels: [70x100]
    page_setup: OFFICE
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    page_setpu: TYPO""",
            "unknown fields",
        ),
    ],
)
def test_config_rejects_malformed_paper_profiles(
    tmp_path: Path, profile: str, message: str
) -> None:
    (tmp_path / "project").mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
allowed_roots: [project]
paper_profiles:
  - {profile}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_config(config_path)


def test_config_rejects_unknown_top_level_field_and_empty_profiles(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "version: 1\nallowed_roots: [project]\nworkspace_rooot: typo\npaper_profiles: []",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown fields"):
        load_config(config_path)

    config_path.write_text(
        "version: 1\nallowed_roots: [project]\npaper_profiles: []",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="between 1 and 100"):
        load_config(config_path)


@pytest.mark.parametrize("workspace", ["project/work", "."])
def test_config_requires_workspace_separate_from_source_trees(
    tmp_path: Path, workspace: str
) -> None:
    (tmp_path / "project").mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
allowed_roots: [project]
workspace_root: {workspace}
paper_profiles:
  - id: sheet
    labels: [70x100]
    page_setup: OFFICE
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be separate"):
        load_config(config_path)


def test_inventory_example_cannot_match_normal_paper_labels() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config = load_config(repository_root / "examples" / "config.inventory.example.yaml")

    assert [profile.id for profile in config.paper_profiles] == ["inventory_only"]
    assert config.require_page_setup_match is True
    assert config.match_paper_profile("A4") is None
    assert config.match_paper_profile("70x100") is None
    assert config.match_paper_profile("700x1000 mm") is None


def test_config_rejects_invalid_utf8_yaml_and_oversized_input(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("version: [1", encoding="utf-8")
    invalid_utf8 = tmp_path / "invalid-utf8.yaml"
    invalid_utf8.write_bytes(b"version: 1\nname: \xff")
    oversized = tmp_path / "oversized.yaml"
    oversized.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))

    for path in (malformed, invalid_utf8):
        with pytest.raises(ValueError, match="valid UTF-8 YAML"):
            load_config(path)
    with pytest.raises(ValueError, match="1 MiB"):
        load_config(oversized)
