from cadplot_mcp.models import (
    DrawingInspection,
    FrameCandidate,
    LayoutSummary,
    PageSetupSummary,
)
from cadplot_mcp.onboarding import build_office_inventory_report


def test_office_inventory_reports_exact_names_without_approving_mapping() -> None:
    inspection = DrawingInspection(
        path=r"C:\Pilot\sheet.dwg",
        frames=[
            FrameCandidate(
                handle="A1",
                layer="PAFTA",
                min_point=(0, 0, 0),
                max_point=(1000, 700, 0),
                label="70x100",
                width_mm=1000,
                height_mm=700,
                confidence=0.98,
            )
        ],
        layouts=[
            LayoutSummary(name="Model", model_type=True),
            LayoutSummary(
                name="PAFTA_70X100",
                model_type=False,
                plotter="OFFICE PDF.pc3",
                media_name="UserDefinedMetric (700.00 x 1000.00MM)",
                plot_style="OFFICE.ctb",
                use_standard_scale=True,
                standard_scale=16,
            ),
        ],
        page_setups=[
            PageSetupSummary(
                name="OFFICE_70X100",
                model_type=False,
                plotter="OFFICE PDF.pc3",
                media_name="UserDefinedMetric (700.00 x 1000.00MM)",
                plot_style="OFFICE.ctb",
                use_standard_scale=True,
                standard_scale=16,
            )
        ],
    )

    report = build_office_inventory_report(inspection)

    assert report["read_only"] is True
    assert report["requires_authorized_mapping"] is True
    assert report["resource_names"] == {
        "frame_labels": ["70x100"],
        "frame_layers": ["PAFTA"],
        "page_setups": ["OFFICE_70X100"],
        "plotters": ["OFFICE PDF.pc3"],
        "plot_styles": ["OFFICE.ctb"],
        "canonical_media": ["UserDefinedMetric (700.00 x 1000.00MM)"],
        "paper_space_layouts": ["PAFTA_70X100"],
    }
    assert report["template_layout_candidates"] == [
        {
            "name": "PAFTA_70X100",
            "plotter": "OFFICE PDF.pc3",
            "plot_style": "OFFICE.ctb",
            "canonical_media": "UserDefinedMetric (700.00 x 1000.00MM)",
            "candidate_only": True,
            "requires_exactly_one_floating_viewport_validation": True,
        }
    ]


def test_office_inventory_exposes_missing_inputs_as_warnings() -> None:
    report = build_office_inventory_report(DrawingInspection(path="empty.dwg"))

    assert len(report["warnings"]) == 3
    assert report["resource_names"]["page_setups"] == []
    assert report["template_layout_candidates"] == []


def test_office_inventory_warns_about_non_one_to_one_page_setup() -> None:
    report = build_office_inventory_report(
        DrawingInspection(
            path="scale-to-fit.dwg",
            page_setups=[
                PageSetupSummary(
                    name="FIT_TO_PAPER",
                    model_type=False,
                    use_standard_scale=True,
                    standard_scale=0,
                )
            ],
        )
    )

    assert any("not verified at 1:1" in warning for warning in report["warnings"])
