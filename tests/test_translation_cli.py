from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadplot_mcp import cli
from cadplot_mcp import translation_cli as local
from cadplot_mcp import translation_service as service
from cadplot_mcp.cli_auth import CLIError
from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.security import PathPolicy
from cadplot_mcp.translation import TextRecord, TextSnapshot


@pytest.fixture
def drawing(tmp_path, monkeypatch):
    source_dir = tmp_path / "drawings"
    source_dir.mkdir()
    source = source_dir / "office.dwg"
    source.write_bytes(b"FAKE-DWG: not licensed CAD evidence")
    config = CadPlotConfig(
        source=tmp_path / "config.yaml",
        path_policy=PathPolicy.from_roots([source_dir]),
        template_path_policy=None,
        paper_profiles=(),
        workspace_root=tmp_path / "outputs",
    )
    monkeypatch.setenv("CADPLOT_ENABLE_TRANSLATION", "1")
    monkeypatch.setattr(local, "load_config", lambda _: config)
    records = [
        TextRecord(
            id=f"{i}:TextString",
            handle=str(i),
            kind="AcDbText",
            block="*Model_Space",
            layer="Notes",
            property="TextString",
            raw="GIRIS" if i == 0 else "OFFICE",
            writable=True,
        )
        for i in range(105)
    ]
    snapshot = TextSnapshot(
        records=records, blockers=[], entity_count=105, structure_sha256="b" * 64
    )
    calls = []

    def worker(request, timeout):
        calls.append(request["mode"])
        if request["mode"] == "scan":
            return snapshot.model_copy(deep=True)
        result = snapshot.model_copy(deep=True)
        for change in request["changes"]:
            next(r for r in result.records if r.id == change["record_id"]).raw = change["after"]
        Path(request["drawing"]).write_bytes(b"FAKE ENGLISH COPY")
        return result

    monkeypatch.setattr(service, "run_text_worker", worker)
    # Local commands must never initialize public credentials or a network MCP session.
    monkeypatch.setattr(
        cli, "CredentialStore", lambda *_: pytest.fail("local command used keyring")
    )
    monkeypatch.setattr(cli, "run", lambda *_: pytest.fail("local command used network path"))
    return source, config, snapshot, calls


def run(capsys, *arguments):
    code = cli.main(["--json", "translate", *map(str, arguments), "--config", "fixture.yaml"])
    captured = capsys.readouterr()
    return code, json.loads(captured.out or captured.err)


def save_review(path, *, reviewed=True):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if reviewed:
        for item in payload["decisions"]:
            item.update(
                source_language="tr" if item["source"] == "GIRIS" else "en",
                target="ENTRANCE" if item["source"] == "GIRIS" else item["source"],
            )
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_full_local_cli_scan_plan_check_apply(drawing, tmp_path, capsys):
    source, config, _, calls = drawing
    original = fingerprint_drawing(source, config.path_policy)
    review_file, plan_file = tmp_path / "review.json", tmp_path / "plan.json"
    code, scan = run(capsys, "scan", source, "--output", review_file)
    assert code == 0 and scan["total_records"] == 105
    assert scan["total_review_spans"] == 105
    assert calls == ["scan"]  # >100 records, no missing pages, no repeated AutoCAD open
    raw = save_review(review_file, reviewed=False)
    assert all(d["source_language"] == "uncertain" for d in raw["decisions"])
    save_review(review_file)
    code, plan = run(capsys, "plan", "--review", review_file, "--output", plan_file)
    assert code == 0 and plan["ready"] and plan["changed_records"] == 1
    code, checked = run(capsys, "check", "--plan", plan_file)
    assert code == 0 and checked["current"]
    code, receipt = run(capsys, "apply", "--plan", plan_file, "--approve", plan["plan_id"])
    assert code == 0 and receipt["source_unchanged"]
    assert receipt["text_roundtrip_verified"] and receipt["visual_review_required"]
    assert not receipt["full_geometry_proven"]
    assert Path(receipt["output_drawing"]).read_bytes() == b"FAKE ENGLISH COPY"
    assert fingerprint_drawing(source, config.path_policy) == original
    assert calls.count("apply") == 1
    assert (Path(receipt["output_drawing"]).parent / "verified-receipt.json").is_file()


def test_unreviewed_plan_has_nonzero_exit_and_never_writes_dwg(drawing, tmp_path, capsys):
    source, _, _, calls = drawing
    review, plan = tmp_path / "review.json", tmp_path / "plan.json"
    run(capsys, "scan", source, "--output", review)
    code, payload = run(capsys, "plan", "--review", review, "--output", plan)
    assert code == 2 and not payload["ready"]
    assert len(payload["blockers"]) == 105
    code, _ = run(capsys, "apply", "--plan", plan, "--approve", payload["plan_id"])
    assert code == 2 and "apply" not in calls


@pytest.mark.parametrize("change", ["decisions", "displayed_plan", "source", "approval"])
def test_changed_review_plan_source_or_approval_refused(drawing, tmp_path, capsys, change):
    source, _, _, calls = drawing
    review, plan_file = tmp_path / "review.json", tmp_path / "plan.json"
    run(capsys, "scan", source, "--output", review)
    save_review(review)
    _, plan = run(capsys, "plan", "--review", review, "--output", plan_file)
    approval = plan["plan_id"]
    bundle = json.loads(plan_file.read_text(encoding="utf-8"))
    if change == "decisions":
        bundle["review"]["decisions"][0]["target"] = "ENTRY"
    elif change == "displayed_plan":
        bundle["plan"]["changes"][0]["after"] = "HIDDEN CHANGE"
    elif change == "source":
        source.write_bytes(b"changed source")
    else:
        approval = "sha256:" + "f" * 64
    plan_file.write_text(json.dumps(bundle), encoding="utf-8")
    code, _ = run(capsys, "apply", "--plan", plan_file, "--approve", approval)
    assert code == 2 and "apply" not in calls


def test_no_silent_overwrite_or_scan_for_existing_output(drawing, tmp_path, capsys):
    source, _, _, calls = drawing
    target = tmp_path / "existing.json"
    target.write_bytes(b"user data")
    code, payload = run(capsys, "scan", source, "--output", target)
    assert code == 2 and payload["error"]["code"] == "invalid_translation_output"
    assert target.read_bytes() == b"user data" and not calls


def test_disabled_requires_explicit_enable_before_loading_config(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("CADPLOT_ENABLE_TRANSLATION", raising=False)
    monkeypatch.setattr(local, "load_config", lambda _: pytest.fail("loaded config while disabled"))
    code, data = run(capsys, "scan", "example.dwg", "--output", tmp_path / "review.json")
    assert code == 2 and data["error"]["code"] == "translation_disabled"


@pytest.mark.parametrize("payload", [b'{"schema_version":1,"schema_version":2}', b"{broken", b"[]"])
def test_invalid_json_redacted(tmp_path, payload):
    target = tmp_path / "review.json"
    target.write_bytes(payload)
    with pytest.raises(CLIError, match="exact schema"):
        local.read_file(str(target), local.ReviewFile)


def test_utf8_bom_and_size_bound(drawing, tmp_path, capsys, monkeypatch):
    source, _, _, _ = drawing
    target = tmp_path / "review.json"
    run(capsys, "scan", source, "--output", target)
    target.write_bytes(b"\xef\xbb\xbf" + target.read_bytes())
    assert local.read_file(str(target), local.ReviewFile).drawing == str(source)
    monkeypatch.setattr(local, "MAX_REVIEW_BYTES", 10)
    with pytest.raises(CLIError):
        local.read_file(str(target), local.ReviewFile)


def test_errors_do_not_echo_company_text(drawing, tmp_path, capsys, monkeypatch):
    source, _, _, _ = drawing

    def fail(*args):
        raise ValueError("CONFIDENTIAL-DRAWING-LABEL")

    monkeypatch.setattr(local, "scan_source", fail)
    code, result = run(capsys, "scan", source, "--output", tmp_path / "review.json")
    assert code == 2 and "CONFIDENTIAL" not in json.dumps(result)


def test_missing_approval_and_invalid_hash_rejected():
    for tail in ([], ["--approve", "yes"], ["--approve", "sha256:wrong"]):
        with pytest.raises(SystemExit):
            cli.parser().parse_args(["translate", "apply", "--plan", "plan.json", *tail])


def test_remote_cli_maps_only_closed_arguments(tmp_path):
    from cadplot_mcp.translation_remote_cli import prepare_arguments

    drawing = "drw_00000000-0000-4000-8000-000000000001"
    inventory = "sha256:" + "a" * 64
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            [
                {
                    "span_id": "A:TextString:0",
                    "source": "GIRIS",
                    "source_language": "tr",
                    "target": "ENTRY",
                }
            ]
        ),
        encoding="utf-8",
    )
    args = cli.parser().parse_args(
        ["translate-remote", "submit", drawing, "--inventory", inventory, "--decisions", str(batch)]
    )
    prepare_arguments(args)
    assert args.tool == "submit_drawing_translations"
    wire = {field: getattr(args, field) for field in args.fields}
    assert "decisions_file" not in wire and str(tmp_path) not in json.dumps(wire)
    assert wire["decisions"][0]["target"] == "ENTRY"


def test_translation_login_scope_is_explicit(monkeypatch):
    import asyncio

    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    from cadplot_mcp.cli_auth import PersistentOAuthProvider

    async def grant(self):
        return self.context.client_metadata.scope

    async def unused(*args):
        raise AssertionError("No network expected")

    monkeypatch.setattr(OAuthClientProvider, "_perform_authorization_code_grant", grant)
    auth = PersistentOAuthProvider(
        "https://example.test/mcp",
        client_metadata=OAuthClientMetadata(
            redirect_uris=["http://127.0.0.1:43817/callback"], scope="cadplot.read"
        ),
        storage=None,
        redirect_handler=unused,
        callback_handler=unused,
    )
    assert asyncio.run(auth._perform_authorization_code_grant()) == "cadplot.read"
    auth.requested_translation_scope = True
    assert asyncio.run(auth._perform_authorization_code_grant()) == "cadplot.read cadplot.translate"
