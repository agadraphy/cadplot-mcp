from __future__ import annotations

import hashlib
import ssl
import sys
import tomllib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from cadplot_protocol.remote_protocol import (
    WorkerTaskEnvelope,
    parse_result_payload,
    serialize_task_payload,
)
from cadplot_protocol.worker_http_protocol import (
    COMPLETE_ROUTE,
    POLL_ROUTE,
    START_ROUTE,
    WorkerControlAck,
    parse_request_proof,
    serialize_control_payload,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cadplot_mcp.local_refs import LocalReferenceStore
from cadplot_mcp.worker_cli import (
    ResolvedWorkerTls,
    WorkerCliError,
    WorkerRuntime,
    _build_tls_context,
    build_worker_runtime,
    load_worker_configuration,
    main,
    run_worker_loop,
)
from cadplot_mcp.worker_crypto import Ed25519Signer, Ed25519Verifier
from cadplot_mcp.worker_runner import WorkerRunOutcome
from cadplot_mcp.worker_transport import HttpResponse, WorkerTransportError


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012x}"


TENANT = _id("tnt")
USER = _id("usr")
DEVICE = _id("ws")
KEY_ID = _id("wkey")
PROJECT = _id("prj")
ORIGIN = "https://gateway.example.com"


class FakeExchange:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send(self, **values: object) -> HttpResponse:
        self.calls.append(values)
        return HttpResponse(
            status=204,
            final_url=ORIGIN + POLL_ROUTE,
            headers={},
            body=b"",
        )


class IdleBackend:
    def validate_environment(self) -> dict[str, Any]:
        raise AssertionError("idle worker must not invoke AutoCAD")

    def scan_project(self, *_args: object, **_kwargs: object) -> list[object]:
        raise AssertionError("idle worker must not scan")

    def inspect(self, _path: Path) -> object:
        raise AssertionError("idle worker must not inspect")

    def plan(self, _path: Path) -> dict[str, Any]:
        raise AssertionError("idle worker must not plan")


def _write_configuration(
    tmp_path: Path,
) -> tuple[
    Path,
    dict[str, object],
    Ed25519PrivateKey,
    Ed25519PrivateKey,
]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    cadplot_config = tmp_path / "cadplot.yaml"
    cadplot_config.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "allowed_roots": [str(project_root)],
                "paper_profiles": [
                    {
                        "id": "office_a3",
                        "labels": ["A3"],
                        "page_setup": "OFFICE_A3",
                        "plotter": "DWG To PDF.pc3",
                        "plot_style": "monochrome.ctb",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    request_private = Ed25519PrivateKey.generate()
    request_private_path = tmp_path / "worker-request-key.pem"
    request_private_path.write_bytes(
        request_private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    gateway_private = Ed25519PrivateKey.generate()
    gateway_public_path = tmp_path / "gateway-dispatch-key.pem"
    gateway_public_path.write_bytes(
        gateway_private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    document: dict[str, object] = {
        "version": 1,
        "gateway_origin": ORIGIN,
        "tenant_id": TENANT,
        "user_id": USER,
        "device_id": DEVICE,
        "key_id": KEY_ID,
        "cadplot_config_path": cadplot_config.name,
        "state_database_path": "worker-state.sqlite3",
        "request_private_key_pem_path": request_private_path.name,
        "gateway_dispatch_public_key_pem_path": gateway_public_path.name,
        "policy_version": 7,
        "poll_interval_seconds": 2,
        "request_timeout_seconds": 15,
        "tls": {},
        "projects": [
            {
                "project_id": PROJECT,
                "alias": "office_a",
                "root": project_root.name,
            }
        ],
    }
    source = tmp_path / "worker.yaml"
    source.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return source, document, request_private, gateway_private


def _rewrite(source: Path, document: Mapping[str, object]) -> None:
    source.write_text(yaml.safe_dump(dict(document), sort_keys=False), encoding="utf-8")


def test_worker_configuration_builds_one_outbound_read_only_iteration(tmp_path: Path) -> None:
    source, _document, request_private, _gateway_private = _write_configuration(tmp_path)

    loaded = load_worker_configuration(source)
    exchange = FakeExchange()
    runtime = build_worker_runtime(
        loaded,
        platform_name="win32",
        exchange=exchange,
        backend=IdleBackend(),  # type: ignore[arg-type]
    )
    events: list[str] = []

    assert run_worker_loop(runtime, once=True, emit=events.append) == 0
    assert events == [WorkerRunOutcome.IDLE.value]
    assert len(exchange.calls) == 1
    call = exchange.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == ORIGIN + POLL_ROUTE
    assert call["timeout_seconds"] == 15
    assert call["max_response_bytes"] > 0
    assert b"path" not in call["body"]  # type: ignore[operator]
    proof = parse_request_proof(call["headers"])  # type: ignore[arg-type]
    assert (proof.tenant_id, proof.device_id, proof.key_id) == (TENANT, DEVICE, KEY_ID)
    assert proof.body_sha256 == hashlib.sha256(call["body"]).hexdigest()  # type: ignore[arg-type]
    assert Ed25519Verifier(request_private.public_key())(
        proof.canonical_signing_bytes(method="POST", route=POLL_ROUTE),
        proof.signature,
    )

    references = LocalReferenceStore(loaded.state_database)
    project = references.resolve_project(
        tenant_id=TENANT,
        device_id=DEVICE,
        project_id=PROJECT,
    )
    assert project.alias == "office_a"
    assert project.canonical_root == (tmp_path / "project").resolve()

    second_runtime = build_worker_runtime(
        loaded,
        platform_name="win32",
        exchange=FakeExchange(),
        backend=IdleBackend(),  # type: ignore[arg-type]
    )
    assert second_runtime.poll_interval_seconds == 2
    assert len(references.list_projects(tenant_id=TENANT, device_id=DEVICE)) == 1


def test_composed_worker_authorizes_starts_and_completes_one_signed_task(tmp_path: Path) -> None:
    source, _document, request_private, gateway_private = _write_configuration(tmp_path)
    now = datetime.now(UTC)
    unsigned = WorkerTaskEnvelope(
        policy_version=7,
        tenant_id=TENANT,
        user_id=USER,
        device_id=DEVICE,
        task_id=_id("tsk"),
        operation_id=_id("op"),
        command_id=_id("cmd"),
        idempotency_key=_id("idem"),
        nonce="dispatch_nonce_00000001",
        issued_at=now,
        expires_at=now + timedelta(seconds=30),
        command={"action": "validate_environment"},
        signature="A" * 86,
    )
    task = WorkerTaskEnvelope.model_validate(
        {
            **unsigned.model_dump(mode="python"),
            "signature": Ed25519Signer(gateway_private)(unsigned.canonical_signing_bytes()),
        }
    )

    class ReadyBackend(IdleBackend):
        def validate_environment(self) -> dict[str, Any]:
            return {
                "ready": True,
                "autocad": {"available": True},
                "plugin": {
                    "connected": True,
                    "inspection_identity_matched": True,
                    "status": {"publishEnabled": False, "runtimeSeries": "R25.0"},
                },
            }

    class TaskExchange:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.result = None

        def send(self, **values: object) -> HttpResponse:
            self.calls.append(values)
            url = values["url"]
            if url == ORIGIN + POLL_ROUTE:
                return HttpResponse(
                    200,
                    str(url),
                    {"content-type": "application/json"},
                    serialize_task_payload(task),
                )
            if url == ORIGIN + START_ROUTE:
                body = serialize_control_payload(
                    WorkerControlAck(
                        task_id=task.task_id,
                        operation_id=task.operation_id,
                        command_id=task.command_id,
                    )
                )
                return HttpResponse(200, str(url), {"content-type": "application/json"}, body)
            if url == ORIGIN + COMPLETE_ROUTE:
                self.result = parse_result_payload(values["body"])  # type: ignore[arg-type]
                assert Ed25519Verifier(request_private.public_key())(
                    self.result.canonical_signing_bytes(),
                    self.result.signature,
                )
                body = serialize_control_payload(
                    WorkerControlAck(
                        task_id=task.task_id,
                        operation_id=task.operation_id,
                        command_id=task.command_id,
                    )
                )
                return HttpResponse(200, str(url), {"content-type": "application/json"}, body)
            raise AssertionError("unexpected outbound route")

    exchange = TaskExchange()
    runtime = build_worker_runtime(
        load_worker_configuration(source),
        platform_name="win32",
        exchange=exchange,
        backend=ReadyBackend(),  # type: ignore[arg-type]
    )

    assert run_worker_loop(runtime, once=True) == 0
    assert [call["url"] for call in exchange.calls] == [
        ORIGIN + POLL_ROUTE,
        ORIGIN + START_ROUTE,
        ORIGIN + COMPLETE_ROUTE,
    ]
    assert exchange.result is not None
    assert exchange.result.result.kind == "validate_environment"
    assert exchange.result.result.ready is True
    assert b"path" not in exchange.calls[-1]["body"]  # type: ignore[operator]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update({"version": 2}),
        lambda document: document.update({"version": True}),
        lambda document: document.update({"unexpected_secret": "do-not-log-me"}),
        lambda document: document.update({"gateway_origin": "http://gateway.example.com"}),
        lambda document: document.update({"request_timeout_seconds": "15"}),
        lambda document: document["projects"][0].update(  # type: ignore[index,union-attr]
            {"local_path": r"C:\private\drawing.dwg"}
        ),
        lambda document: document["projects"][0].update(  # type: ignore[index,union-attr]
            {"project_id": r"\\server\share"}
        ),
        lambda document: document.update({"tls": {"client_certificate_path": "client.pem"}}),
    ],
)
def test_worker_yaml_rejects_unknown_coerced_remote_and_partial_tls_values(
    tmp_path: Path,
    mutation: Any,
) -> None:
    source, document, _private, _gateway_private = _write_configuration(tmp_path)
    mutation(document)
    _rewrite(source, document)

    with pytest.raises(WorkerCliError) as captured:
        load_worker_configuration(source)

    assert captured.value.code == "worker_config_invalid"
    assert str(captured.value) == "worker_config_invalid"
    assert "do-not-log-me" not in str(captured.value)
    assert "private" not in str(captured.value)


def test_worker_yaml_rejects_duplicate_keys_and_too_many_projects(tmp_path: Path) -> None:
    source, document, _private, _gateway_private = _write_configuration(tmp_path)
    source.write_text(
        yaml.safe_dump(document, sort_keys=False)
        + "gateway_origin: https://do-not-log-me.example\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkerCliError, match="^worker_config_invalid$"):
        load_worker_configuration(source)

    document["projects"] = [
        {
            "project_id": _id("prj", index + 1),
            "alias": f"project_{index}",
            "root": "project",
        }
        for index in range(101)
    ]
    _rewrite(source, document)
    with pytest.raises(WorkerCliError, match="^worker_config_invalid$"):
        load_worker_configuration(source)


def test_projects_must_stay_inside_the_local_cadplot_path_policy(tmp_path: Path) -> None:
    source, document, _private, _gateway_private = _write_configuration(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    document["projects"] = [
        {
            "project_id": PROJECT,
            "alias": "outside",
            "root": outside.name,
        }
    ]
    _rewrite(source, document)

    with pytest.raises(WorkerCliError, match="^worker_config_invalid$"):
        load_worker_configuration(source)


def test_invalid_private_key_and_cli_errors_never_echo_material_or_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    source, _document, _private, _gateway_private = _write_configuration(tmp_path)
    secret = "TOP-SECRET-PRIVATE-KEY-MATERIAL"
    (tmp_path / "worker-request-key.pem").write_text(secret, encoding="utf-8")

    with pytest.raises(WorkerCliError) as captured:
        load_worker_configuration(source)
    assert captured.value.code == "worker_config_invalid"
    assert secret not in str(captured.value)

    assert main(["--config", str(source), "--once"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "cadplot-worker: worker_config_invalid\n"
    assert secret not in output.err
    assert str(source) not in output.err


def test_main_rejects_non_windows_before_reading_or_echoing_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    secret_path = r"C:\TOP-SECRET\worker.yaml"

    assert main(["--config", secret_path, "--once"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "cadplot-worker: windows_worker_required\n"
    assert secret_path not in output.err


def test_argument_errors_do_not_echo_unknown_secret_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "TOP-SECRET-ARGUMENT"

    with pytest.raises(SystemExit) as captured:
        main(["--config", "worker.yaml", "--token", secret])

    assert captured.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "cadplot-worker: arguments_invalid\n"
    assert secret not in output.err


def test_live_runtime_fails_closed_off_windows_before_state_or_network(tmp_path: Path) -> None:
    source, _document, _private, _gateway_private = _write_configuration(tmp_path)
    loaded = load_worker_configuration(source)
    exchange = FakeExchange()

    with pytest.raises(WorkerCliError, match="^windows_worker_required$"):
        build_worker_runtime(
            loaded,
            platform_name="linux",
            exchange=exchange,
            backend=IdleBackend(),  # type: ignore[arg-type]
        )

    assert exchange.calls == []
    assert not loaded.state_database.exists()


def test_default_live_tls_context_requires_hostname_and_certificate_validation() -> None:
    context = _build_tls_context(
        ResolvedWorkerTls(
            ca_bundle=None,
            client_certificate=None,
            client_private_key=None,
        )
    )

    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.keylog_filename is None


def test_continuous_loop_uses_fixed_bounded_interval_and_safe_events() -> None:
    class SequencedRunner:
        def __init__(self) -> None:
            self.calls = 0

        def run_once(self) -> WorkerRunOutcome:
            self.calls += 1
            if self.calls == 1:
                raise WorkerTransportError("transport_unavailable")
            return WorkerRunOutcome.COMPLETED

    runner = SequencedRunner()
    runtime = WorkerRuntime(runner=runner, poll_interval_seconds=3)  # type: ignore[arg-type]
    sleeps: list[float] = []
    events: list[str] = []

    def stop_after_two(value: float) -> None:
        sleeps.append(value)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_worker_loop(
            runtime,
            once=False,
            sleep=stop_after_two,
            emit=events.append,
        )

    assert runner.calls == 2
    assert sleeps == [3, 3]
    assert events == ["worker_iteration_failed", "completed"]


def test_package_exposes_cadplot_worker_console_script() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"]["cadplot-worker"] == "cadplot_mcp.worker_cli:main"
