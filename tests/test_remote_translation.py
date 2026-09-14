from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cadplot_protocol.translation_protocol import (
    ApplyTranslation,
    PlanTranslation,
    RemoteDecision,
    ScanTexts,
    SubmitReview,
    TranslationCommand,
)

from cadplot_mcp import remote_translation as remote
from cadplot_mcp import translation_service as service
from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.security import PathPolicy
from cadplot_mcp.translation import TextRecord, TextSnapshot


def opaque(prefix, number=1):
    return f"{prefix}_00000000-0000-4000-8000-{number:012x}"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    source = root / "drawing.dwg"
    source.write_bytes(b"fixture-only")
    config = CadPlotConfig(
        source=tmp_path / "config.yaml",
        path_policy=PathPolicy.from_roots([root]),
        template_path_policy=None,
        paper_profiles=(),
        workspace_root=tmp_path / "out",
    )
    monkeypatch.setattr(remote, "load_config", lambda _: config)
    monkeypatch.setenv("CADPLOT_ENABLE_TRANSLATION", "1")
    snap = TextSnapshot(
        records=[
            TextRecord(
                id="A:TextString",
                handle="A",
                kind="AcDbMText",
                block="*Model_Space",
                layer="Notes",
                property="TextString",
                raw="GIRIS",
                writable=True,
            )
        ],
        blockers=[],
        entity_count=1,
        structure_sha256="b" * 64,
    )
    writes = []

    def worker(request, _):
        result = snap.model_copy(deep=True)
        if request["mode"] == "apply":
            writes.append(request)
            result.records[0].raw = request["changes"][0]["after"]
            Path(request["drawing"]).write_bytes(b"translated-fixture")
        return result

    monkeypatch.setattr(service, "run_text_worker", worker)
    reference = SimpleNamespace(
        project_id=opaque("prj"), drawing_id=opaque("drw"), local_file=source
    )
    backend = remote.RemoteTranslationBackend(
        "config.yaml", tmp_path / "translation.db", [reference.project_id]
    )

    def run(request, **identity):
        envelope = SimpleNamespace(
            tenant_id=opaque("tnt"),
            user_id=opaque("usr"),
            device_id=opaque("ws"),
            policy_version=1,
            command=TranslationCommand(drawing_id=reference.drawing_id, request=request),
        )
        for key, value in identity.items():
            setattr(envelope, key, value)
        return backend.execute(envelope, reference).response

    return run, backend, source, writes, snap


def reviewed_plan(harness):
    run, *_ = harness
    scan = run(ScanTexts())
    decision = RemoteDecision(
        span_id=scan.records[0].spans[0].id, source="GIRIS", source_language="tr", target="ENTRANCE"
    )
    request = SubmitReview(inventory_id=scan.inventory_id, decisions=[decision])
    review = run(request)
    assert run(request) == review  # exact request replay does not duplicate decisions
    plan = run(PlanTranslation(inventory_id=scan.inventory_id, review_id=review.review_id))
    assert plan.ready and plan.changed_records == 1
    return ApplyTranslation(
        inventory_id=scan.inventory_id,
        review_id=review.review_id,
        approved_plan_id=plan.plan_id,
        plan_operation_id=opaque("op"),
    )


def test_remote_flow_is_copy_only_and_durable_non_replaying(harness):
    run, backend, source, writes, _ = harness
    request = reviewed_plan(harness)
    receipt = run(request)
    assert receipt.source_unchanged and receipt.visual_review_required
    assert source.read_bytes() == b"fixture-only"
    assert len(writes) == 1
    assert run(request) == receipt and len(writes) == 1
    assert "output_drawing" not in receipt.model_dump()
    assert "drawing.dwg" not in receipt.model_dump_json()
    # Reopen the durable store; this is not just an in-memory dedupe.
    assert backend.database.is_file()


def test_interrupted_write_is_never_replayed(harness, monkeypatch):
    run, _, _, writes, _ = harness
    request = reviewed_plan(harness)
    attempts = []

    def interrupted(*args):
        attempts.append(1)
        raise RuntimeError("uncertain CAD outcome")

    monkeypatch.setattr(remote, "apply_translation", interrupted)
    for _ in range(2):
        with pytest.raises(remote.TranslationAttentionRequired):
            run(request)
    assert attempts == [1] and not writes


@pytest.mark.parametrize(
    "identity",
    [
        {"user_id": opaque("usr", 2)},
        {"tenant_id": opaque("tnt", 2)},
        {"device_id": opaque("ws", 2)},
        {"policy_version": 2},
    ],
)
def test_plan_cannot_cross_identity_or_policy(harness, identity):
    run, _, _, writes, _ = harness
    request = reviewed_plan(harness)
    with pytest.raises(ValueError, match="plan_missing"):
        run(request, **identity)
    assert not writes


def test_feature_and_project_consent_required_before_scan(harness, monkeypatch):
    run, backend, *_ = harness
    monkeypatch.delenv("CADPLOT_ENABLE_TRANSLATION")
    with pytest.raises(ValueError, match="not_authorized"):
        run(ScanTexts())
    monkeypatch.setenv("CADPLOT_ENABLE_TRANSLATION", "1")
    backend.project_ids = frozenset()
    with pytest.raises(ValueError, match="not_authorized"):
        run(ScanTexts())


def test_review_conflict_invalid_span_and_source_change(harness):
    run, _, source, _, _ = harness
    scan = run(ScanTexts())
    first = RemoteDecision(
        span_id=scan.records[0].spans[0].id, source="GIRIS", source_language="tr", target="ENTRY"
    )
    review = run(SubmitReview(inventory_id=scan.inventory_id, decisions=[first]))
    second = first.model_copy(update={"target": "ENTRANCE"})
    with pytest.raises(ValueError, match="review_conflict"):
        run(SubmitReview(inventory_id=scan.inventory_id, decisions=[second]))
    updated = run(
        SubmitReview(
            inventory_id=scan.inventory_id, expected_review_id=review.review_id, decisions=[second]
        )
    )
    assert updated.review_id != review.review_id
    with pytest.raises(ValueError, match="review_changed"):
        run(PlanTranslation(inventory_id=scan.inventory_id, review_id=review.review_id))
    source.write_bytes(b"modified")
    with pytest.raises(ValueError, match="changed"):
        run(PlanTranslation(inventory_id=scan.inventory_id, review_id=updated.review_id))


def test_remote_text_keeps_formatting_but_has_no_local_control_fields(harness):
    run, _, _, _, snap = harness
    snap.records[0].raw = r"{\C1;YANGIN CIKISI\P80/210}"
    scan = run(ScanTexts())
    assert scan.records[0].raw == snap.records[0].raw
    assert scan.records[0].spans[0].text == "YANGIN CIKISI"
    assert not {"path", "drawing", "roots", "workspace_root", "progid"} & set(
        json.loads(scan.model_dump_json())
    )
