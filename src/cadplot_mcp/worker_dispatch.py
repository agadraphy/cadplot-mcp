from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from cadplot_protocol.remote_protocol import (
    CreatePublishPlanCommand,
    DrawingsResult,
    DrawingSummary,
    EnvironmentResult,
    ErrorResult,
    FrameSummary,
    InspectDrawingCommand,
    InspectionResult,
    LayoutSummary,
    ListProjectsCommand,
    PageSetupSummary,
    PlanSheetSummary,
    ProjectsResult,
    ProjectSummary,
    PublishPlanResult,
    ReadOnlyAction,
    ScanDrawingsCommand,
    ValidateEnvironmentCommand,
    WorkerResult,
    WorkerResultEnvelope,
    WorkerTaskEnvelope,
)

from cadplot_mcp.backends.autocad_com import AutoCADUnavailableError
from cadplot_mcp.backends.isolated_autocad import AutoCADInspectionTimeoutError
from cadplot_mcp.config import CadPlotConfig, load_config
from cadplot_mcp.discovery import scan_drawings
from cadplot_mcp.environment import diagnose_environment
from cadplot_mcp.local_refs import LocalReferenceError, LocalReferenceStore
from cadplot_mcp.models import DrawingFile, DrawingInspection
from cadplot_mcp.server import _build_current_plan, _inspector

_SIGNATURE_PLACEHOLDER = "A" * 86


class LocalReadBackend(Protocol):
    def validate_environment(self) -> dict[str, Any]: ...

    def scan_project(
        self,
        root: Path,
        *,
        recursive: bool,
        limit: int,
    ) -> list[DrawingFile]: ...

    def inspect(self, path: Path) -> DrawingInspection: ...

    def plan(self, path: Path) -> dict[str, Any]: ...


class DefaultLocalReadBackend:
    """Adapter over the existing local deterministic core; no remote settings are accepted."""

    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path).expanduser().resolve(strict=True)
        self._config: CadPlotConfig = load_config(self._config_path)

    def validate_environment(self) -> dict[str, Any]:
        return diagnose_environment(self._config_path, mode="full")

    def scan_project(
        self,
        root: Path,
        *,
        recursive: bool,
        limit: int,
    ) -> list[DrawingFile]:
        return scan_drawings(
            root,
            self._config.path_policy,
            recursive=recursive,
            max_files=limit,
        )

    def inspect(self, path: Path) -> DrawingInspection:
        return _inspector(self._config).inspect_drawing(path)

    def plan(self, path: Path) -> dict[str, Any]:
        return _build_current_plan(str(path), self._config)


class WorkerDispatcher:
    """Authorize and execute one closed read-only command on the licensed workstation."""

    def __init__(
        self,
        *,
        tenant_id: str,
        user_id: str,
        device_id: str,
        policy_version: int,
        references: LocalReferenceStore,
        backend: LocalReadBackend,
    ) -> None:
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._device_id = device_id
        self._policy_version = policy_version
        self._references = references
        self._backend = backend

    def execute(
        self,
        envelope: WorkerTaskEnvelope,
        *,
        now: datetime,
        verify_gateway_signature: Callable[[bytes, str], bool],
        accept_nonce: Callable[[str], bool],
        sign_worker_result: Callable[[bytes], str],
        completion_clock: Callable[[], datetime] | None = None,
        on_authorized: Callable[[], object] | None = None,
    ) -> WorkerResultEnvelope:
        envelope.authorize_for_worker(
            expected_tenant_id=self._tenant_id,
            expected_user_id=self._user_id,
            expected_device_id=self._device_id,
            expected_policy_version=self._policy_version,
            now=now,
            verify_signature=verify_gateway_signature,
            accept_nonce=accept_nonce,
        )
        if on_authorized is not None:
            on_authorized()
        result = self._execute_authorized(envelope)
        completed_at = completion_clock() if completion_clock is not None else now
        unsigned = WorkerResultEnvelope(
            tenant_id=envelope.tenant_id,
            user_id=envelope.user_id,
            device_id=envelope.device_id,
            task_id=envelope.task_id,
            operation_id=envelope.operation_id,
            command_id=envelope.command_id,
            completed_at=completed_at,
            result=result,
            signature=_SIGNATURE_PLACEHOLDER,
        )
        signature = sign_worker_result(unsigned.canonical_signing_bytes())
        return WorkerResultEnvelope.model_validate(
            {**unsigned.model_dump(mode="python"), "signature": signature}
        )

    def _execute_authorized(self, envelope: WorkerTaskEnvelope) -> WorkerResult:
        command = envelope.command
        action: ReadOnlyAction = command.action
        try:
            if isinstance(command, ValidateEnvironmentCommand):
                return self._environment_result()
            if isinstance(command, ListProjectsCommand):
                return self._projects_result()
            if isinstance(command, ScanDrawingsCommand):
                return self._drawings_result(command)
            if isinstance(command, InspectDrawingCommand):
                return self._inspection_result(command)
            if isinstance(command, CreatePublishPlanCommand):
                return self._plan_result(command)
        except LocalReferenceError:
            return _error(action, "drawing_not_found", retryable=False)
        except AutoCADInspectionTimeoutError:
            return _error(action, "operation_timeout", retryable=True)
        except AutoCADUnavailableError:
            return _error(action, "autocad_unavailable", retryable=True)
        except FileNotFoundError:
            return _error(action, "drawing_not_found", retryable=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            code = "plan_blocked" if action == "create_publish_plan" else "inspection_failed"
            return _error(action, code, retryable=False)
        except Exception:
            return _error(action, "worker_failure", retryable=False, attention_required=True)
        return _error(action, "worker_failure", retryable=False, attention_required=True)

    def _environment_result(self) -> EnvironmentResult:
        report = self._backend.validate_environment()
        autocad = report.get("autocad") if isinstance(report.get("autocad"), dict) else {}
        plugin = report.get("plugin") if isinstance(report.get("plugin"), dict) else {}
        status = plugin.get("status") if isinstance(plugin.get("status"), dict) else {}
        error_codes: list[str] = []
        if autocad.get("available") is not True:
            error_codes.append("autocad_unavailable")
        if plugin.get("connected") is not True:
            error_codes.append("plugin_unavailable")
        if plugin.get("inspection_identity_matched") is False:
            error_codes.append("runtime_mismatch")
        if report.get("ready") is not True and not error_codes:
            error_codes.append("environment_not_ready")
        runtime = status.get("runtimeSeries")
        return EnvironmentResult(
            ready=report.get("ready") is True,
            autocad_connected=autocad.get("available") is True,
            plugin_connected=plugin.get("connected") is True,
            publish_enabled=status.get("publishEnabled") is True,
            runtime_series=runtime if isinstance(runtime, str) else None,
            inspection_identity_matched=plugin.get("inspection_identity_matched") is True,
            error_codes=error_codes,
        )

    def _projects_result(self) -> ProjectsResult:
        projects = self._references.list_projects(
            tenant_id=self._tenant_id,
            device_id=self._device_id,
        )
        return ProjectsResult(
            projects=[
                ProjectSummary(
                    project_id=project.project_id,
                    alias=project.alias,
                    display_name=project.alias,
                )
                for project in projects
            ]
        )

    def _drawings_result(self, command: ScanDrawingsCommand) -> DrawingsResult:
        if command.cursor is not None:
            raise ValueError("Cursor pagination is not enabled in protocol version 1.")
        project = self._references.resolve_project(
            tenant_id=self._tenant_id,
            device_id=self._device_id,
            project_id=command.project_id,
        )
        drawings = self._backend.scan_project(
            project.canonical_root,
            recursive=command.recursive,
            limit=command.limit,
        )
        summaries: list[DrawingSummary] = []
        catalog_items: list[dict[str, Any]] = []
        for drawing in drawings:
            local_file = Path(drawing.path).resolve(strict=True)
            relative = local_file.relative_to(project.canonical_root).as_posix()
            reference = self._references.get_or_register_drawing(
                tenant_id=self._tenant_id,
                device_id=self._device_id,
                project_id=project.project_id,
                relative_drawing=relative,
            )
            modified = datetime.fromisoformat(drawing.modified_utc)
            summary = DrawingSummary(
                drawing_id=reference.drawing_id,
                display_name=_safe_display(local_file.name, fallback="drawing.dwg"),
                size_bytes=drawing.size_bytes,
                modified_utc=modified,
            )
            summaries.append(summary)
            catalog_items.append(summary.model_dump(mode="json"))
        revision = hashlib.sha256(
            json.dumps(catalog_items, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return DrawingsResult(
            project_id=project.project_id,
            catalog_revision=revision,
            drawings=summaries,
        )

    def _inspection_result(self, command: InspectDrawingCommand) -> InspectionResult:
        reference = self._references.resolve_drawing(
            tenant_id=self._tenant_id,
            device_id=self._device_id,
            drawing_id=command.drawing_id,
        )
        inspection = self._backend.inspect(reference.local_file)
        return InspectionResult(
            drawing_id=reference.drawing_id,
            layouts=[
                LayoutSummary(
                    name=_safe_display(layout.name, fallback="redacted_layout"),
                    model_type=layout.model_type,
                    plotter=_optional_display(layout.plotter),
                    media_name=_optional_display(layout.media_name),
                    plot_style=_optional_display(layout.plot_style),
                )
                for layout in inspection.layouts
            ],
            page_setups=[
                PageSetupSummary(
                    name=_safe_display(setup.name, fallback="redacted_setup"),
                    model_type=setup.model_type,
                    plotter=_optional_display(setup.plotter),
                    media_name=_optional_display(setup.media_name),
                    plot_style=_optional_display(setup.plot_style),
                )
                for setup in inspection.page_setups
            ],
            frames=[
                FrameSummary(
                    handle=frame.handle,
                    layer=_safe_display(frame.layer, fallback="redacted_layer"),
                    label=_safe_display(frame.label, fallback="redacted_label"),
                    min_point=frame.min_point,
                    max_point=frame.max_point,
                    confidence=frame.confidence,
                )
                for frame in inspection.frames
            ],
            notice_codes=["inspection_notice"] if inspection.warnings else [],
        )

    def _plan_result(self, command: CreatePublishPlanCommand) -> PublishPlanResult:
        reference = self._references.resolve_drawing(
            tenant_id=self._tenant_id,
            device_id=self._device_id,
            drawing_id=command.drawing_id,
        )
        plan = self._backend.plan(reference.local_file)
        fingerprint = plan.get("drawing_fingerprint")
        if not isinstance(fingerprint, dict):
            raise ValueError("Plan fingerprint is unavailable.")
        raw_sheets = plan.get("sheets")
        if not isinstance(raw_sheets, list):
            raise ValueError("Plan sheets are unavailable.")
        sheets: list[PlanSheetSummary] = []
        for index, sheet in enumerate(raw_sheets, start=1):
            if not isinstance(sheet, dict):
                raise ValueError("Plan sheet is invalid.")
            profile = sheet.get("profile")
            geometry = sheet.get("plot_geometry")
            sheets.append(
                PlanSheetSummary(
                    sheet_index=index,
                    frame_handle=sheet.get("frame_handle"),
                    label=_safe_display(sheet.get("label"), fallback="redacted_label"),
                    status=sheet.get("status"),
                    profile_id=profile.get("id") if isinstance(profile, dict) else None,
                    target_layout=_safe_display(
                        sheet.get("target_layout"), fallback=f"CADPLOT_{index:04d}"
                    ),
                    scale_denominator=(
                        geometry.get("scale_denominator") if isinstance(geometry, dict) else None
                    ),
                )
            )
        warnings = plan.get("warnings")
        return PublishPlanResult(
            drawing_id=reference.drawing_id,
            plan_id=plan.get("plan_id"),
            ready=plan.get("ready") is True,
            drawing_sha256=fingerprint.get("sha256"),
            sheets=sheets,
            notice_codes=["plan_notice"] if isinstance(warnings, list) and warnings else [],
        )


def _safe_display(value: object, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(value.split())
    if (
        not cleaned
        or len(cleaned) > 255
        or "/" in cleaned
        or "\\" in cleaned
        or "\x00" in cleaned
        or (len(cleaned) >= 2 and cleaned[0].isalpha() and cleaned[1] == ":")
    ):
        return fallback
    return cleaned


def _optional_display(value: object) -> str | None:
    if value is None:
        return None
    cleaned = _safe_display(value, fallback="")
    return cleaned or None


def _error(
    action: ReadOnlyAction,
    code: str,
    *,
    retryable: bool,
    attention_required: bool = False,
) -> ErrorResult:
    return ErrorResult(
        action=action,
        code=code,
        retryable=retryable,
        attention_required=attention_required,
    )
