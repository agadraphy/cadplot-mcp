from cadplot_mcp.batch import build_batch_page


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
