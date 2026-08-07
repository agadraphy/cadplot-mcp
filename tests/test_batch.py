import pytest

from cadplot_mcp.batch import build_batch_page, stage_approved_batch


def _plan(path: str) -> dict:
    if path.endswith("error.dwg"):
        raise RuntimeError("AutoCAD inspection failed")
    ready = not path.endswith("blocked.dwg")
    return {
        "plan_id": "sha256:" + ("a" if ready else "b") * 64,
        "drawing": path,
        "ready": ready,
    }


def test_batch_page_is_paginated_and_restartable() -> None:
    paths = [f"C:/project/{index:03d}.dwg" for index in range(45)]

    first = build_batch_page(paths, _plan, offset=0, limit=20)
    second = build_batch_page(paths, _plan, offset=first["next_offset"], limit=20)
    last = build_batch_page(paths, _plan, offset=second["next_offset"], limit=20)

    assert first["processed"] == 20
    assert first["has_more"] is True
    assert second["offset"] == 20
    assert last["processed"] == 5
    assert last["has_more"] is False
    assert last["next_offset"] is None


def test_batch_page_isolates_blockers_and_errors() -> None:
    paths = ["ready.dwg", "blocked.dwg", "error.dwg", "also-ready.dwg"]

    page = build_batch_page(paths, _plan)

    assert page["summary"] == {"ready": 2, "blocked": 1, "errors": 1}
    assert [item["status"] for item in page["items"]] == [
        "ready",
        "blocked",
        "error",
        "ready",
    ]
    assert page["items"][2]["error"] == "AutoCAD inspection failed"


def test_batch_page_id_is_deterministic() -> None:
    paths = ["one.dwg", "two.dwg"]

    first = build_batch_page(paths, _plan)
    second = build_batch_page(paths, _plan)

    assert first == second
    assert first["batch_page_id"].startswith("sha256:")


def test_batch_page_beyond_end_is_empty() -> None:
    page = build_batch_page(["one.dwg"], _plan, offset=10, limit=5)

    assert page["processed"] == 0
    assert page["has_more"] is False
    assert page["next_offset"] is None


def test_batch_page_rejects_unbounded_requests() -> None:
    for limit in (0, 51):
        try:
            build_batch_page([], _plan, limit=limit)
        except ValueError as exc:
            assert "between 1 and 50" in str(exc)
        else:
            raise AssertionError("unsafe limit was accepted")


def test_stage_approved_batch_stages_only_exact_current_plans() -> None:
    plan_ids = {
        "one.dwg": "sha256:" + "1" * 64,
        "two.dwg": "sha256:" + "2" * 64,
    }
    staged: list[str] = []

    result = stage_approved_batch(
        [
            {"path": "one.dwg", "plan_id": plan_ids["one.dwg"]},
            {"path": "two.dwg", "plan_id": plan_ids["two.dwg"]},
        ],
        lambda path: {"drawing": path, "plan_id": plan_ids[path], "ready": True},
        lambda plan, _: staged.append(plan["drawing"]) or {"job_id": plan["drawing"]},
    )

    assert result["complete"] is True
    assert result["summary"] == {"requested": 2, "staged": 2, "blocked": 0, "errors": 0}
    assert staged == ["one.dwg", "two.dwg"]


def test_stage_approved_batch_isolates_mismatch_blocker_and_error() -> None:
    approved = [
        {"path": "changed.dwg", "plan_id": "sha256:" + "1" * 64},
        {"path": "blocked.dwg", "plan_id": "sha256:" + "2" * 64},
        {"path": "error.dwg", "plan_id": "sha256:" + "3" * 64},
    ]

    def build(path: str) -> dict:
        if path == "changed.dwg":
            return {"ready": True, "plan_id": "sha256:" + "9" * 64}
        if path == "blocked.dwg":
            return {"ready": False, "plan_id": approved[1]["plan_id"]}
        raise RuntimeError("inspection failed")

    result = stage_approved_batch(approved, build, lambda *_: {"unexpected": True})

    assert result["complete"] is False
    assert result["summary"] == {"requested": 3, "staged": 0, "blocked": 2, "errors": 1}
    assert [item["status"] for item in result["items"]] == [
        "approval_mismatch",
        "blocked",
        "error",
    ]


def test_stage_approved_batch_rejects_duplicates_before_writing() -> None:
    plan_id = "sha256:" + "a" * 64
    writes: list[str] = []

    with pytest.raises(ValueError, match="paths must be unique"):
        stage_approved_batch(
            [
                {"path": "same.dwg", "plan_id": plan_id},
                {"path": "SAME.dwg", "plan_id": "sha256:" + "b" * 64},
            ],
            lambda path: {"ready": True, "plan_id": plan_id, "drawing": path},
            lambda plan, _: writes.append(plan["drawing"]) or {},
        )

    assert writes == []


def test_stage_approved_batch_rejects_more_than_twenty() -> None:
    approvals = [
        {"path": f"{index}.dwg", "plan_id": "sha256:" + f"{index:064x}"}
        for index in range(21)
    ]

    with pytest.raises(ValueError, match="between 1 and 20"):
        stage_approved_batch(approvals, lambda _: {}, lambda *_: {})
