from pathlib import Path

from cadplot_mcp.demo import run_synthetic_demo


def test_synthetic_demo_exercises_approved_copy_and_pdf_audit(tmp_path: Path) -> None:
    result = run_synthetic_demo(tmp_path)

    assert result["synthetic"] is True
    assert result["source_unchanged"] is True
    assert result["plan_ready"] is True
    assert result["job_state"] == "staged"
    assert result["audit_complete"] is True
    assert result["audit_summary"] == {"expected": 1, "valid": 1, "missing": 0, "invalid": 0}
    assert result["pdf"]["page_count"] == 1
    assert result["pdf"]["status"] == "valid"
