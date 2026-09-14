"""Opt-in outbound worker translation with durable review and non-replayable writes."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cadplot_protocol.translation_protocol import (
    ApplyResult,
    ApplyTranslation,
    Change,
    PlanResult,
    PlanTranslation,
    Record,
    ReviewResult,
    ScanResult,
    ScanTexts,
    SubmitReview,
    TranslationResult,
)

from cadplot_mcp.config import load_config
from cadplot_mcp.security import FILE_ATTRIBUTE_REPARSE_POINT, require_plain_directory_path
from cadplot_mcp.translation import (
    TranslationDecision,
    build_translation_plan,
    digest,
    inventory_page,
)
from cadplot_mcp.translation_service import apply_translation, prepare_plan, scan_source


class TranslationAttentionRequired(RuntimeError):
    pass


def _codes(blockers):
    return sorted({item.split(":", 1)[0] for item in blockers})


def _display(value):
    if (
        not value
        or len(value) > 255
        or any(c in value for c in "/\\:")
        or any(ord(c) < 32 for c in value)
    ):
        return "redacted"
    return value


class RemoteTranslationBackend:
    def __init__(self, config_path, database, project_ids):
        self.config_path = config_path
        self.database = Path(database)
        self.project_ids = frozenset(project_ids)

    @contextmanager
    def _connect(self):
        require_plain_directory_path(self.database.parent)
        if self.database.is_symlink() or (
            self.database.exists()
            and getattr(self.database.lstat(), "st_file_attributes", 0)
            & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("translation_state_redirected")
        connection = sqlite3.connect(self.database, timeout=10)
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS reviews (
                scope TEXT NOT NULL, inventory TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(scope, inventory));
            CREATE TABLE IF NOT EXISTS plans (
                scope TEXT NOT NULL, plan_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(scope, plan_id));
            CREATE TABLE IF NOT EXISTS writes (
                scope TEXT NOT NULL, plan_id TEXT NOT NULL, receipt TEXT,
                PRIMARY KEY(scope, plan_id));
        """)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def execute(self, envelope, reference):
        if (
            os.environ.get("CADPLOT_ENABLE_TRANSLATION") != "1"
            or reference.project_id not in self.project_ids
        ):
            raise ValueError("translation_project_not_authorized")
        config = load_config(self.config_path)
        request = envelope.command.request
        source = str(reference.local_file)
        # User/device/policy separation also applies to cached reviews and write receipts.
        scope = digest(
            [
                envelope.tenant_id,
                envelope.user_id,
                envelope.device_id,
                envelope.policy_version,
                reference.drawing_id,
            ]
        )
        if isinstance(request, ScanTexts):
            name, fingerprint, snapshot = scan_source(source, config)
            page = inventory_page(
                name,
                fingerprint,
                snapshot,
                expected_id=request.inventory_id,
                offset=request.offset,
                limit=request.limit,
            )
            result = ScanResult(
                inventory_id=page.inventory_id,
                total_records=page.total_records,
                total_review_spans=page.total_review_spans,
                offset=page.offset,
                next_offset=page.next_offset,
                blocker_count=len(page.blockers),
                blocker_codes=_codes(page.blockers),
                records=[
                    Record(
                        id=r.id,
                        kind=r.kind,
                        block=_display(r.block),
                        layer=_display(r.layer),
                        raw=r.raw,
                        spans=[s.model_dump() for s in r.spans],
                        writable=r.writable,
                    )
                    for r in page.records
                ],
            )
        elif isinstance(request, SubmitReview):
            name, fingerprint, snapshot = scan_source(source, config)
            decisions = [TranslationDecision(**d.model_dump()) for d in request.decisions]
            # Validate exact source/span/target before persisting any submitted text.
            build_translation_plan(name, fingerprint, snapshot, request.inventory_id, decisions)
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT payload FROM reviews WHERE scope=? AND inventory=?",
                    (scope, request.inventory_id),
                ).fetchone()
                current = json.loads(row[0]) if row else []
                previous_id = self._review_id(request.inventory_id, current) if row else None
                by_id = {d["span_id"]: d for d in current}
                exact_retry = all(by_id.get(d.span_id) == d.model_dump() for d in decisions)
                if request.expected_review_id != previous_id and not exact_retry:
                    raise ValueError("translation_review_conflict")
                by_id.update({d.span_id: d.model_dump() for d in decisions})
                current = [by_id[key] for key in sorted(by_id)]
                if len(current) > 100_000 or len(json.dumps(current)) > 8 * 1024 * 1024:
                    raise ValueError("translation_review_too_large")
                db.execute(
                    "INSERT OR REPLACE INTO reviews VALUES (?, ?, ?)",
                    (scope, request.inventory_id, json.dumps(current)),
                )
            result = ReviewResult(
                inventory_id=request.inventory_id,
                review_id=self._review_id(request.inventory_id, current),
                reviewed_spans=len(current),
            )
        elif isinstance(request, PlanTranslation):
            with self._connect() as db:
                row = db.execute(
                    "SELECT payload FROM reviews WHERE scope=? AND inventory=?",
                    (scope, request.inventory_id),
                ).fetchone()
            current = json.loads(row[0]) if row else []
            if self._review_id(request.inventory_id, current) != request.review_id:
                raise ValueError("translation_review_changed")
            decisions = [TranslationDecision(**d) for d in current]
            plan = prepare_plan(source, config, request.inventory_id, decisions)
            if request.offset > len(plan.changes):
                raise ValueError("translation_page_invalid")
            payload = json.dumps(
                {"plan": plan.model_dump(), "decisions": current, "review_id": request.review_id}
            )
            with self._connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO plans VALUES (?, ?, ?)", (scope, plan.plan_id, payload)
                )
            end = request.offset + request.limit
            result = PlanResult(
                inventory_id=plan.inventory_id,
                review_id=request.review_id,
                plan_id=plan.plan_id,
                ready=plan.ready,
                changed_records=len(plan.changes),
                reviewed_spans=plan.reviewed_spans,
                offset=request.offset,
                next_offset=end if end < len(plan.changes) else None,
                changes=[Change(**c.model_dump()) for c in plan.changes[request.offset : end]],
                blocker_count=len(plan.blockers),
                blocker_codes=_codes(plan.blockers),
            )
        elif isinstance(request, ApplyTranslation):
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT payload FROM plans WHERE scope=? AND plan_id=?",
                    (scope, request.approved_plan_id),
                ).fetchone()
                if not row:
                    raise ValueError("translation_plan_missing")
                stored = json.loads(row[0])
                if (
                    stored["review_id"] != request.review_id
                    or stored["plan"]["inventory_id"] != request.inventory_id
                ):
                    raise ValueError("translation_plan_mismatch")
                if not stored["plan"]["ready"] or not stored["plan"]["changes"]:
                    raise ValueError("translation_plan_blocked")
                previous = db.execute(
                    "SELECT receipt FROM writes WHERE scope=? AND plan_id=?",
                    (scope, request.approved_plan_id),
                ).fetchone()
                if previous:
                    if previous[0] is None:
                        raise TranslationAttentionRequired("translation_write_outcome_unknown")
                    return TranslationResult(
                        drawing_id=reference.drawing_id,
                        response=ApplyResult.model_validate_json(previous[0]),
                    )
                # Durable BEFORE any mutation; a crash without a receipt never replays the write.
                db.execute(
                    "INSERT INTO writes VALUES (?, ?, NULL)", (scope, request.approved_plan_id)
                )
            try:
                receipt = apply_translation(
                    source,
                    config,
                    request.inventory_id,
                    [TranslationDecision(**d) for d in stored["decisions"]],
                    request.approved_plan_id,
                )
                result = ApplyResult(
                    inventory_id=request.inventory_id,
                    review_id=request.review_id,
                    plan_id=receipt.plan_id,
                    output_id=Path(receipt.output_drawing).parent.name,
                    output_sha256=receipt.output_sha256,
                    changed_records=receipt.changed_records,
                    source_unchanged=receipt.source_unchanged,
                    text_roundtrip_verified=receipt.text_roundtrip_verified,
                    inspected_structure_unchanged=receipt.inspected_structure_unchanged,
                )
                with self._connect() as db:
                    db.execute(
                        "UPDATE writes SET receipt=? WHERE scope=? AND plan_id=?",
                        (result.model_dump_json(), scope, request.approved_plan_id),
                    )
            except Exception:
                raise TranslationAttentionRequired("translation_write_needs_local_review") from None
        else:
            raise ValueError("translation_operation_unsupported")
        response = TranslationResult(drawing_id=reference.drawing_id, response=result)
        # Leave room for the signed envelope and HTTP operation metadata. Oversized pages
        # fail as bounded worker errors; never strand an undeliverable pending result.
        if len(response.model_dump_json().encode("utf-8")) > 512 * 1024:
            raise ValueError("translation_page_too_large_reduce_limit")
        return response

    @staticmethod
    def _review_id(inventory, decisions):
        return "sha256:" + digest([inventory, decisions])
