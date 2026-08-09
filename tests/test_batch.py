import pytest

from cadplot_mcp.batch import (
    build_batch_page,
    build_drawing_inventory_id,
    queue_approved_batch,
    stage_approved_batch,
)


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
    second = build_batch_page(
        paths,
        _plan,
        offset=first["next_offset"],
        limit=20,
        expected_inventory_id=first["inventory_id"],
    )
    last = build_batch_page(
        paths,
        _plan,
        offset=second["next_offset"],
        limit=20,
        expected_inventory_id=first["inventory_id"],
    )

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
    first = build_batch_page(["one.dwg"], _plan, limit=5)
    page = build_batch_page(
        ["one.dwg"],
        _plan,
        offset=10,
        limit=5,
        expected_inventory_id=first["inventory_id"],
    )

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


def test_batch_page_requires_matching_inventory_after_first_page() -> None:
    paths = ["one.dwg", "two.dwg"]
    first = build_batch_page(paths, _plan, limit=1)

    with pytest.raises(ValueError, match="required after the first"):
        build_batch_page(paths, _plan, offset=1, limit=1)
    with pytest.raises(ValueError, match="inventory changed"):
        build_batch_page(
            paths,
            _plan,
            offset=1,
            limit=1,
            expected_inventory_id="sha256:" + "0" * 64,
        )

    second = build_batch_page(
        paths,
        _plan,
        offset=1,
        limit=1,
        expected_inventory_id=first["inventory_id"],
    )
    assert second["inventory_id"] == first["inventory_id"]


def test_drawing_inventory_id_changes_with_metadata_not_order() -> None:
    first = [
        {"path": "B.dwg", "size_bytes": 20, "modified_utc": "2026-08-08T10:00:00Z"},
        {"path": "a.dwg", "size_bytes": 10, "modified_utc": "2026-08-08T09:00:00Z"},
    ]
    reordered = list(reversed(first))
    changed = [*first[:1], {**first[1], "size_bytes": 11}]

    assert build_drawing_inventory_id(first) == build_drawing_inventory_id(reordered)
    assert build_drawing_inventory_id(first) != build_drawing_inventory_id(changed)


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


def test_queue_approved_batch_isolates_per_job_failures() -> None:
    approvals = [
        {
            "manifest_path": f"job-{index}/manifest.json",
            "plan_id": "sha256:" + f"{index:064x}",
            "manifest_sha256": f"{index + 10:064x}",
        }
        for index in range(1, 4)
    ]

    result = queue_approved_batch(
        approvals,
        lambda approval: (
            {"queued": False, "error": "publish_disabled"}
            if approval["plan_id"] == approvals[1]["plan_id"]
            else {"queued": True}
        ),
    )

    assert result["complete"] is False
    assert result["schema_version"] == 2
    assert result["summary"] == {
        "requested": 3,
        "queued": 2,
        "deferred": 0,
        "failed": 1,
    }
    assert result["items"][1]["error"] == "publish_disabled"


def test_queue_approved_batch_defers_remaining_items_after_queue_saturation() -> None:
    approvals = [
        {
            "manifest_path": f"job-{index}/manifest.json",
            "plan_id": "sha256:" + f"{index:064x}",
            "manifest_sha256": f"{index + 20:064x}",
        }
        for index in range(1, 5)
    ]
    called: list[str] = []

    def queue(approval: dict[str, str]) -> dict[str, object]:
        called.append(approval["plan_id"])
        if len(called) == 2:
            return {"queued": False, "plugin": {"ok": False, "error": "queue_full"}}
        return {"queued": True, "plugin": {"ok": True}}

    result = queue_approved_batch(approvals, queue)

    assert called == [approvals[0]["plan_id"], approvals[1]["plan_id"]]
    assert result["complete"] is False
    assert result["summary"] == {
        "requested": 4,
        "queued": 1,
        "deferred": 3,
        "failed": 0,
    }
    assert [item.get("deferred") for item in result["items"]] == [None, True, True, True]
    assert all(item.get("error") == "queue_full" for item in result["items"][1:])


def test_queue_approved_batch_rejects_duplicate_manifest_approvals() -> None:
    approval = {
        "manifest_path": "job/manifest.json",
        "plan_id": "sha256:" + "a" * 64,
        "manifest_sha256": "b" * 64,
    }

    with pytest.raises(ValueError, match="manifest paths must be unique"):
        queue_approved_batch(
            [
                approval,
                {
                    **approval,
                    "manifest_path": "JOB/manifest.json",
                    "plan_id": "sha256:" + "c" * 64,
                    "manifest_sha256": "d" * 64,
                },
            ],
            lambda _: {"queued": True},
        )
