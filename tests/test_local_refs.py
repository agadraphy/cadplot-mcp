from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cadplot_protocol.remote_protocol import EnvironmentResult, WorkerResultEnvelope

from cadplot_mcp.local_refs import LocalReferenceError, LocalReferenceStore


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012d}"


def _store(tmp_path: Path) -> LocalReferenceStore:
    return LocalReferenceStore(tmp_path / "references.sqlite3")


def _registered_project(
    tmp_path: Path,
    *,
    tenant_id: str | None = None,
    device_id: str | None = None,
    project_id: str | None = None,
) -> tuple[LocalReferenceStore, Path, str, str, str]:
    tenant_id = tenant_id or _id("tnt")
    device_id = device_id or _id("ws")
    project_id = project_id or _id("prj")
    root = tmp_path / "project"
    root.mkdir()
    store = _store(tmp_path)
    store.register_project(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        alias="office_a",
        root=root,
    )
    return store, root, tenant_id, device_id, project_id


def test_project_and_drawing_references_resolve_only_to_local_canonical_files(
    tmp_path: Path,
) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    drawing = root / "discipline" / "sheet.dwg"
    drawing.parent.mkdir()
    drawing.write_bytes(b"dwg")

    registered = store.register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        drawing_id=_id("drw"),
        relative_drawing="discipline/sheet.dwg",
    )
    resolved = store.resolve_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        drawing_id=_id("drw"),
    )

    assert registered == resolved
    assert resolved.project_alias == "office_a"
    assert resolved.relative_drawing == "discipline/sheet.dwg"
    assert resolved.local_file == drawing.resolve()
    assert store.list_projects(tenant_id=tenant_id, device_id=device_id) == (
        store.resolve_project(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
        ),
    )


def test_dwt_is_supported_but_other_suffixes_are_rejected(tmp_path: Path) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    template = root / "office.dwt"
    template.write_bytes(b"dwt")
    notes = root / "notes.txt"
    notes.write_text("no", encoding="utf-8")

    resolved = store.register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        drawing_id=_id("drw"),
        relative_drawing="office.dwt",
    )
    assert resolved.local_file == template.resolve()
    with pytest.raises(LocalReferenceError, match="dwg or .dwt"):
        store.register_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            drawing_id=_id("drw", 2),
            relative_drawing="notes.txt",
        )


def test_discovery_returns_one_stable_opaque_reference(tmp_path: Path) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    (root / "sheet.dwg").write_bytes(b"dwg")
    generated = uuid.UUID("00000000-0000-4000-8000-000000000099")

    first = store.get_or_register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        relative_drawing="sheet.dwg",
        id_factory=lambda: generated,
    )
    second = store.get_or_register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        relative_drawing="sheet.dwg",
        id_factory=lambda: uuid.uuid4(),
    )

    assert first == second
    assert first.drawing_id == f"drw_{generated}"


@pytest.mark.parametrize(
    "unsafe",
    [
        "../outside.dwg",
        "folder/../../outside.dwg",
        "/absolute/sheet.dwg",
        r"C:\Clients\sheet.dwg",
        r"\\server\share\sheet.dwg",
        "folder\\sheet.dwg",
        "folder/./sheet.dwg",
        "folder:/sheet.dwg",
    ],
)
def test_relative_drawing_rejects_traversal_windows_and_unc_locations(
    tmp_path: Path,
    unsafe: str,
) -> None:
    store, _root, tenant_id, device_id, project_id = _registered_project(tmp_path)

    with pytest.raises(LocalReferenceError):
        store.register_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            drawing_id=_id("drw"),
            relative_drawing=unsafe,
        )


def test_cross_tenant_and_cross_device_resolution_is_denied(tmp_path: Path) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    drawing = root / "sheet.dwg"
    drawing.write_bytes(b"dwg")
    store.register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        drawing_id=_id("drw"),
        relative_drawing="sheet.dwg",
    )

    with pytest.raises(LocalReferenceError, match="unavailable"):
        store.resolve_project(
            tenant_id=_id("tnt", 2),
            device_id=device_id,
            project_id=project_id,
        )
    with pytest.raises(LocalReferenceError, match="unavailable"):
        store.resolve_drawing(
            tenant_id=tenant_id,
            device_id=_id("ws", 2),
            drawing_id=_id("drw"),
        )


def test_reference_identifiers_cannot_be_locations(tmp_path: Path) -> None:
    store, _root, tenant_id, device_id, project_id = _registered_project(tmp_path)

    with pytest.raises(LocalReferenceError, match="Invalid drawing identifier"):
        store.resolve_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            drawing_id=r"C:\Clients\sheet.dwg",
        )
    with pytest.raises(LocalReferenceError, match="Invalid project identifier"):
        store.resolve_project(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=r"\\server\share",
        )
    with pytest.raises(LocalReferenceError, match="Invalid tenant identifier"):
        store.resolve_project(
            tenant_id="../../tenant",
            device_id=device_id,
            project_id=project_id,
        )


def test_project_and_drawing_uniqueness_fail_closed(tmp_path: Path) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    other_root = tmp_path / "other"
    other_root.mkdir()

    for duplicate in (
        {
            "project_id": project_id,
            "alias": "office_b",
            "root": other_root,
        },
        {
            "project_id": _id("prj", 2),
            "alias": "office_a",
            "root": other_root,
        },
        {
            "project_id": _id("prj", 3),
            "alias": "office_c",
            "root": root,
        },
    ):
        with pytest.raises(LocalReferenceError, match="conflicts"):
            store.register_project(
                tenant_id=tenant_id,
                device_id=device_id,
                **duplicate,
            )

    drawing = root / "Sheet.dwg"
    drawing.write_bytes(b"dwg")
    store.register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        drawing_id=_id("drw"),
        relative_drawing="Sheet.dwg",
    )
    with pytest.raises(LocalReferenceError, match="conflicts"):
        store.register_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            drawing_id=_id("drw", 2),
            relative_drawing="Sheet.dwg",
        )


def test_ensure_project_is_exactly_idempotent_and_rejects_mapping_changes(
    tmp_path: Path,
) -> None:
    tenant_id = _id("tnt")
    device_id = _id("ws")
    project_id = _id("prj")
    root = tmp_path / "project"
    other_root = tmp_path / "other"
    root.mkdir()
    other_root.mkdir()
    store = _store(tmp_path)
    values = {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "project_id": project_id,
        "alias": "Office_A",
        "root": root,
    }

    first = store.ensure_project(**values)
    second = LocalReferenceStore(tmp_path / "references.sqlite3").ensure_project(**values)

    assert first == second
    assert first.canonical_root == root.resolve()
    for changed in (
        values | {"alias": "Office_B"},
        values | {"root": other_root},
        values | {"tenant_id": _id("tnt", 2)},
    ):
        with pytest.raises(LocalReferenceError, match="conflicts"):
            store.ensure_project(**changed)
    with pytest.raises(LocalReferenceError, match="conflicts"):
        store.ensure_project(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=_id("prj", 2),
            alias="office_a",
            root=other_root,
        )


def test_database_tampering_to_a_traversal_fails_on_resolve(tmp_path: Path) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    drawing = root / "sheet.dwg"
    drawing.write_bytes(b"dwg")
    store.register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        drawing_id=_id("drw"),
        relative_drawing="sheet.dwg",
    )
    database = tmp_path / "references.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE local_drawings SET relative_drawing = ? WHERE drawing_id = ?",
            ("../outside.dwg", _id("drw")),
        )

    with pytest.raises(LocalReferenceError, match="invalid"):
        store.resolve_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            drawing_id=_id("drw"),
        )


def test_path_policy_is_revalidated_after_registration(tmp_path: Path) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    drawing = root / "sheet.dwg"
    drawing.write_bytes(b"dwg")
    store.register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        drawing_id=_id("drw"),
        relative_drawing="sheet.dwg",
    )
    drawing.unlink()

    with pytest.raises(LocalReferenceError):
        store.resolve_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            drawing_id=_id("drw"),
        )


def test_symlink_escape_is_rejected_on_register_and_resolve(tmp_path: Path) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_drawing = outside / "sheet.dwg"
    outside_drawing.write_bytes(b"dwg")
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(LocalReferenceError, match="redirect|outside"):
        store.register_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            drawing_id=_id("drw"),
            relative_drawing="escape/sheet.dwg",
        )

    link.unlink()
    local = root / "local"
    local.mkdir()
    local_drawing = local / "sheet.dwg"
    local_drawing.write_bytes(b"dwg")
    store.register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        drawing_id=_id("drw"),
        relative_drawing="local/sheet.dwg",
    )
    local_drawing.unlink()
    local.rmdir()
    try:
        local.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink replacement is unavailable: {exc}")

    with pytest.raises(LocalReferenceError, match="redirect|outside"):
        store.resolve_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            drawing_id=_id("drw"),
        )


def test_redirected_component_guard_fails_closed_without_os_symlink_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    redirected = root / "redirected"
    redirected.mkdir()
    (redirected / "sheet.dwg").write_bytes(b"dwg")
    original = LocalReferenceStore._is_redirect
    monkeypatch.setattr(
        LocalReferenceStore,
        "_is_redirect",
        staticmethod(lambda value: value.name == "redirected" or original(value)),
    )

    with pytest.raises(LocalReferenceError, match="filesystem redirect"):
        store.register_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            drawing_id=_id("drw"),
            relative_drawing="redirected/sheet.dwg",
        )


def test_project_directory_must_be_plain_and_canonical(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    store = _store(tmp_path)

    with pytest.raises(LocalReferenceError, match="project directory"):
        store.register_project(
            tenant_id=_id("tnt"),
            device_id=_id("ws"),
            project_id=_id("prj"),
            alias="office_a",
            root=redirected,
        )


def test_store_does_not_depend_on_network_or_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CADPLOT_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("CADPLOT_GATEWAY_URL", raising=False)
    store, root, tenant_id, device_id, project_id = _registered_project(tmp_path)
    drawing = root / "sheet.dwg"
    drawing.write_bytes(os.urandom(8))

    reference = store.register_drawing(
        tenant_id=tenant_id,
        device_id=device_id,
        project_id=project_id,
        drawing_id=_id("drw"),
        relative_drawing="sheet.dwg",
    )
    assert reference.local_file == drawing.resolve()


def test_dispatch_nonce_is_consumed_atomically_and_survives_reopen(tmp_path: Path) -> None:
    database = tmp_path / "references.sqlite3"
    store = LocalReferenceStore(database)
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    arguments = {
        "tenant_id": _id("tnt"),
        "device_id": _id("ws"),
        "nonce": "n" * 22,
        "now": now,
        "expires_at": now + timedelta(seconds=30),
    }

    assert store.consume_dispatch_nonce(**arguments)
    assert not LocalReferenceStore(database).consume_dispatch_nonce(**arguments)

    later = now + timedelta(seconds=31)
    assert LocalReferenceStore(database).consume_dispatch_nonce(
        **(arguments | {"now": later, "expires_at": later + timedelta(seconds=30)})
    )


def test_dispatch_nonce_deadlines_and_values_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    with pytest.raises(LocalReferenceError, match="nonce"):
        store.consume_dispatch_nonce(
            tenant_id=_id("tnt"),
            device_id=_id("ws"),
            nonce="short",
            now=now,
            expires_at=now + timedelta(seconds=30),
        )
    with pytest.raises(LocalReferenceError, match="deadline"):
        store.consume_dispatch_nonce(
            tenant_id=_id("tnt"),
            device_id=_id("ws"),
            nonce="n" * 22,
            now=now,
            expires_at=now + timedelta(minutes=6),
        )


def test_signed_result_outbox_is_durable_idempotent_and_exact(tmp_path: Path) -> None:
    database = tmp_path / "references.sqlite3"
    store = LocalReferenceStore(database)
    completed = datetime(2026, 9, 2, 12, tzinfo=UTC)
    result = WorkerResultEnvelope(
        tenant_id=_id("tnt"),
        user_id=_id("usr"),
        device_id=_id("ws"),
        task_id=_id("tsk"),
        operation_id=_id("op"),
        command_id=_id("cmd"),
        completed_at=completed,
        result=EnvironmentResult(
            ready=True,
            autocad_connected=True,
            plugin_connected=True,
            publish_enabled=False,
            runtime_series="R25.0",
            inspection_identity_matched=True,
        ),
        signature="A" * 86,
    )

    store.save_pending_result(result)
    store.save_pending_result(result)
    reopened = LocalReferenceStore(database)
    assert (
        reopened.next_pending_result(
            tenant_id=result.tenant_id,
            device_id=result.device_id,
        )
        == result
    )

    changed = result.model_copy(
        update={"result": result.result.model_copy(update={"ready": False})}
    )
    with pytest.raises(LocalReferenceError, match="conflicts"):
        reopened.save_pending_result(changed)

    reopened.acknowledge_pending_result(result)
    assert (
        reopened.next_pending_result(
            tenant_id=result.tenant_id,
            device_id=result.device_id,
        )
        is None
    )
    with pytest.raises(LocalReferenceError, match="acknowledgement"):
        reopened.acknowledge_pending_result(result)
