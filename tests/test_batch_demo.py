from pathlib import Path

import pytest

from cadplot_mcp.batch_demo import canonical_batch_demo_digest, run_synthetic_batch_demo


def test_synthetic_batch_demo_rehearses_bounded_scale_without_live_claims(
    tmp_path: Path,
) -> None:
    result = run_synthetic_batch_demo(
        tmp_path,
        drawing_count=25,
        batch_size=20,
        report_page_size=10,
    )

    assert result["synthetic"] is True
    assert result["target_drawings"] == 25
    assert result["planning"] == {
        "page_size": 20,
        "pages": 2,
        "processed": 25,
        "ready": 25,
        "blocked": 0,
        "errors": 0,
        "unique_plan_ids": 25,
    }
    assert result["staging"] == {
        "batch_size": 20,
        "batches": 2,
        "staged": 25,
        "unique_job_ids": 25,
    }
    assert result["queue_protocol_rehearsal"] == {
        "synthetic": True,
        "batch_size": 20,
        "queue_capacity": 7,
        "waves": 4,
        "approvals": 25,
        "simulated_acceptances": 25,
        "deferred_results": 19,
        "pipe_attempts": 27,
        "exact_retry_identity_preserved": True,
        "status_batches": 2,
        "status_items": 25,
        "status_pending": 25,
        "status_identity_preserved": True,
        "plugin_contacted": False,
    }
    assert result["restart_report_before_outputs"]["awaiting_execution"] == 25
    assert result["output_audit"] == {
        "audited_jobs": 25,
        "outputs_complete": 25,
        "execution_verified": 0,
        "publish_verified": 0,
        "receipts_created": False,
        "orientation_mismatch_rejected": True,
    }
    assert result["restart_report_after_outputs"]["manual_review"] == 25
    assert result["restart_report_after_outputs"]["complete"] == 0
    assert result["source_unchanged"] is True
    assert result["company_assets_used"] is False
    assert result["autocad_launched"] is False
    assert result["live_publish_proven"] is False
    assert canonical_batch_demo_digest(result).startswith("sha256:")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("drawing_count", 0, "drawing_count"),
        ("batch_size", 21, "batch_size"),
        ("report_page_size", 51, "report_page_size"),
    ],
)
def test_synthetic_batch_demo_rejects_unbounded_inputs(
    tmp_path: Path, field: str, value: int, message: str
) -> None:
    arguments = {"drawing_count": 1, "batch_size": 1, "report_page_size": 1}
    arguments[field] = value

    with pytest.raises(ValueError, match=message):
        run_synthetic_batch_demo(tmp_path, **arguments)
