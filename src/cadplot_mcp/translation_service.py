"""Local orchestration: isolated COM, exact approval, and copy-only output."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from cadplot_mcp.backends.autocad_com import resolve_autocad_progid
from cadplot_mcp.config import CadPlotConfig
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.security import PathPolicy, require_plain_directory_path
from cadplot_mcp.translation import (
    TextSnapshot,
    TranslationDecision,
    TranslationPlan,
    TranslationReceipt,
    build_translation_plan,
)

MAX_WIRE_BYTES = 8 * 1024 * 1024


def run_text_worker(request: dict, timeout: int) -> TextSnapshot:
    encoded = json.dumps(request, ensure_ascii=True).encode()
    if len(encoded) > MAX_WIRE_BYTES:
        raise ValueError("Translation payload exceeds 8 MiB; nothing was changed.")
    try:
        process = subprocess.run(
            [sys.executable, "-m", "cadplot_mcp.translation_worker"],
            input=encoded,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Translation helper timed out. A private copy may remain open in AutoCAD; "
            "close it without saving before retrying. No verified output was released."
        ) from None
    if len(process.stdout) > MAX_WIRE_BYTES:
        raise RuntimeError("Translation worker output exceeded the safety limit.")
    try:
        response = json.loads(process.stdout)
        if process.returncode or set(response) != {"ok", "snapshot", "error"}:
            raise ValueError("invalid protocol")
        if response["ok"] is not True:
            raise RuntimeError(str(response["error"])[:500])
        return TextSnapshot.model_validate(response["snapshot"])
    except (ValueError, TypeError):
        raise RuntimeError("Invalid translation worker response.") from None


def request_for(path: Path, policy: PathPolicy, config: CadPlotConfig) -> dict:
    return {
        "mode": "scan",
        "drawing": str(path),
        "roots": [str(root) for root in policy.allowed_roots],
        "progid": resolve_autocad_progid()[0],
        "source_sha256": None,
        "snapshot": None,
        "changes": [],
    }


def scan_source(path: str, config: CadPlotConfig) -> tuple[str, dict, TextSnapshot]:
    source = config.path_policy.require_allowed(path, suffix=".dwg")
    before = fingerprint_drawing(path, config.path_policy)
    snapshot = run_text_worker(
        request_for(source, config.path_policy, config), config.inspection_timeout_seconds
    )
    if fingerprint_drawing(path, config.path_policy) != before:
        raise ValueError("Source changed during inventory; restart the complete scan.")
    return str(source), before, snapshot


def prepare_plan(
    path: str,
    config: CadPlotConfig,
    expected_inventory_id: str,
    decisions: list[TranslationDecision],
) -> TranslationPlan:
    source, fingerprint, snapshot = scan_source(path, config)
    return build_translation_plan(source, fingerprint, snapshot, expected_inventory_id, decisions)


def apply_translation(
    path: str,
    config: CadPlotConfig,
    expected_inventory_id: str,
    decisions: list[TranslationDecision],
    approved_plan_id: str,
) -> TranslationReceipt:
    if os.environ.get("CADPLOT_ENABLE_TRANSLATION") != "1":
        raise ValueError("Restart local MCP with CADPLOT_ENABLE_TRANSLATION=1 after authorization.")
    if config.workspace_root is None:
        raise ValueError("A private workspace_root is required for English copies.")
    source_name, before, snapshot = scan_source(path, config)
    plan = build_translation_plan(source_name, before, snapshot, expected_inventory_id, decisions)
    if plan.plan_id != approved_plan_id or not plan.ready:
        raise ValueError("Exact current translation plan approval with zero blockers is required.")
    if not plan.changes:
        raise ValueError("The reviewed drawing has no requested Turkish text changes.")
    source = Path(source_name)
    workspace = require_plain_directory_path(config.workspace_root)
    workspace.mkdir(parents=True, exist_ok=True)
    if workspace.resolve(strict=True) != workspace:
        raise ValueError("Workspace path changed.")
    job = workspace / f"translation-{uuid.uuid4().hex}"
    job.mkdir(exist_ok=False)
    draft_dir = job / "unverified"
    draft_dir.mkdir()
    draft = draft_dir / "working.dwg"
    audit_path = job / "translation-audit.json"
    # Keep recovery data and failed private copies. Never erase source or previous output.
    with audit_path.open("x", encoding="utf-8") as audit:
        json.dump(
            {
                "status": "unverified",
                "plan": plan.model_dump(),
                "source_fingerprint": before,
                "decisions": [decision.model_dump() for decision in decisions],
            },
            audit,
            ensure_ascii=True,
            indent=2,
        )
    with source.open("rb") as input_file, draft.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
    policy = PathPolicy.from_roots([draft_dir])
    if (
        fingerprint_drawing(draft, policy)["sha256"] != before["sha256"]
        or fingerprint_drawing(path, config.path_policy) != before
    ):
        raise ValueError("Source changed while creating the translation copy.")
    request = request_for(draft, policy, config)
    request.update(
        mode="apply",
        source_sha256=before["sha256"],
        snapshot=snapshot.model_dump(),
        changes=[change.model_dump() for change in plan.changes],
    )
    after = run_text_worker(request, config.inspection_timeout_seconds)
    desired = {record.id: record.model_copy() for record in snapshot.records}
    for change in plan.changes:
        desired[change.record_id].raw = change.after
    expected_after = snapshot.model_copy(update={"records": list(desired.values())})
    if after != expected_after or fingerprint_drawing(path, config.path_policy) != before:
        raise ValueError(
            "Translation verification failed; inspect the unverified job, not an output."
        )
    draft_fingerprint = fingerprint_drawing(draft, policy)
    # Exclusive creation: no source or existing English copy can be overwritten.
    result = job / f"{source.stem}.EN.dwg"
    with draft.open("rb") as input_file, result.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
    result_fingerprint = fingerprint_drawing(result, PathPolicy.from_roots([job]))
    if (
        result_fingerprint["sha256"] != draft_fingerprint["sha256"]
        or fingerprint_drawing(path, config.path_policy) != before
    ):
        raise ValueError("Final copy verification failed; no completion receipt was written.")
    receipt = TranslationReceipt(
        plan_id=plan.plan_id,
        source_drawing=source_name,
        output_drawing=str(result),
        audit_file=str(audit_path),
        source_unchanged=True,
        changed_records=len(plan.changes),
        output_sha256=result_fingerprint["sha256"],
        text_roundtrip_verified=True,
        inspected_structure_unchanged=True,
    )
    with (job / "verified-receipt.json").open("x", encoding="utf-8") as output_file:
        json.dump(receipt.model_dump(), output_file, ensure_ascii=True, indent=2)
    return receipt
