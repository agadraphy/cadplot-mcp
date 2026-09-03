"""Closed, path-free wire models shared by the gateway and workstation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

PROTOCOL_VERSION = 1
WORKER_AUDIENCE = "cadplot-worker"
SIGNATURE_ALGORITHM = "ed25519"
MAX_TASK_LIFETIME = timedelta(minutes=5)
MAX_CLOCK_SKEW = timedelta(seconds=30)
MAX_TASK_PAYLOAD_BYTES = 256 * 1024
MAX_RESULT_PAYLOAD_BYTES = 8 * 1024 * 1024

_UUID4 = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_UUID1_TO_5 = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_SAFE_CODE = r"^[a-z][a-z0-9_]{0,63}$"
_SHA256 = r"^[0-9a-f]{64}$"
_PLAN_ID = r"^sha256:[0-9a-f]{64}$"
_SIGNATURE = r"^[A-Za-z0-9_-]{86}$"
_NONCE = r"^[A-Za-z0-9_-]{22,86}$"


def _opaque_id(prefix: str, uuid_fragment: str = _UUID4) -> str:
    return rf"^{prefix}_{uuid_fragment}$"


TenantId = Annotated[
    str,
    Field(pattern=_opaque_id("tnt", _UUID1_TO_5), max_length=40),
]
UserId = Annotated[
    str,
    Field(pattern=_opaque_id("usr", _UUID1_TO_5), max_length=40),
]
DeviceId = Annotated[str, Field(pattern=_opaque_id("ws"), max_length=39)]
TaskId = Annotated[str, Field(pattern=_opaque_id("tsk"), max_length=40)]
OperationId = Annotated[str, Field(pattern=_opaque_id("op"), max_length=39)]
CommandId = Annotated[str, Field(pattern=_opaque_id("cmd"), max_length=40)]
IdempotencyKey = Annotated[str, Field(pattern=_opaque_id("idem"), max_length=41)]
ProjectId = Annotated[str, Field(pattern=_opaque_id("prj"), max_length=40)]
DrawingId = Annotated[str, Field(pattern=_opaque_id("drw"), max_length=40)]
CursorId = Annotated[str, Field(pattern=_opaque_id("cur"), max_length=40)]
PlanId = Annotated[str, Field(pattern=_PLAN_ID, min_length=71, max_length=71)]
Sha256 = Annotated[str, Field(pattern=_SHA256, min_length=64, max_length=64)]
SafeCode = Annotated[str, Field(pattern=_SAFE_CODE, max_length=64)]
SafeAlias = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", max_length=64),
]


def _safe_display(value: str) -> str:
    if value != value.strip() or not value:
        raise ValueError("Display value must be non-empty and trimmed.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Display value contains a control character.")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("Display value contains a separator.")
    if re.match(r"^[A-Za-z]:", value) or value.startswith(("//", "\\\\")):
        raise ValueError("Display value must not be an absolute location.")
    return value


def _drawing_display(value: str) -> str:
    value = _safe_display(value)
    if not value.casefold().endswith((".dwg", ".dwt")):
        raise ValueError("Drawing display name must end in .dwg or .dwt.")
    return value


SafeDisplay = Annotated[str, Field(min_length=1, max_length=255), AfterValidator(_safe_display)]
DrawingDisplay = Annotated[
    str,
    Field(min_length=5, max_length=255),
    AfterValidator(_drawing_display),
]
RuntimeSeries = Annotated[str, Field(pattern=r"^R[0-9]{2}\.[0-9]$", max_length=5)]
NoticeCodes = Annotated[list[SafeCode], Field(max_length=5_000)]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ValidateEnvironmentCommand(_ClosedModel):
    action: Literal["validate_environment"] = "validate_environment"


class ListProjectsCommand(_ClosedModel):
    action: Literal["list_projects"] = "list_projects"


class ScanDrawingsCommand(_ClosedModel):
    action: Literal["scan_drawings"] = "scan_drawings"
    project_id: ProjectId
    recursive: bool = True
    cursor: CursorId | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50


class InspectDrawingCommand(_ClosedModel):
    action: Literal["inspect_drawing"] = "inspect_drawing"
    drawing_id: DrawingId


class CreatePublishPlanCommand(_ClosedModel):
    action: Literal["create_publish_plan"] = "create_publish_plan"
    drawing_id: DrawingId


ReadOnlyCommand = Annotated[
    ValidateEnvironmentCommand
    | ListProjectsCommand
    | ScanDrawingsCommand
    | InspectDrawingCommand
    | CreatePublishPlanCommand,
    Field(discriminator="action"),
]


class WorkerTaskEnvelope(_ClosedModel):
    protocol_version: Literal[PROTOCOL_VERSION] = PROTOCOL_VERSION
    audience: Literal[WORKER_AUDIENCE] = WORKER_AUDIENCE
    policy_version: Annotated[int, Field(ge=1, le=2_147_483_647)]
    tenant_id: TenantId
    user_id: UserId
    device_id: DeviceId
    task_id: TaskId
    operation_id: OperationId
    command_id: CommandId
    idempotency_key: IdempotencyKey
    nonce: Annotated[str, Field(pattern=_NONCE, min_length=22, max_length=86)]
    issued_at: datetime
    expires_at: datetime
    command: ReadOnlyCommand
    signature_algorithm: Literal[SIGNATURE_ALGORITHM] = SIGNATURE_ALGORITHM
    signature: Annotated[str, Field(pattern=_SIGNATURE, min_length=86, max_length=86)]

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Dispatch timestamps must be UTC and timezone-aware.")
        return value

    @model_validator(mode="after")
    def _validate_lifetime(self) -> WorkerTaskEnvelope:
        lifetime = self.expires_at - self.issued_at
        if lifetime <= timedelta(0) or lifetime > MAX_TASK_LIFETIME:
            raise ValueError("Dispatch lifetime is invalid.")
        return self

    def canonical_signing_bytes(self) -> bytes:
        """Return the deterministic complete dispatch body covered by the gateway signature."""
        payload = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def authorize_for_worker(
        self,
        *,
        expected_tenant_id: str,
        expected_user_id: str,
        expected_device_id: str,
        expected_policy_version: int,
        now: datetime,
        verify_signature: Callable[[bytes, str], bool],
        accept_nonce: Callable[[str], bool],
    ) -> None:
        """Validate dispatch authority before any opaque reference is resolved locally."""
        valid_now = now.tzinfo is not None and now.utcoffset() == timedelta(0)
        context_matches = (
            self.tenant_id == expected_tenant_id
            and self.user_id == expected_user_id
            and self.device_id == expected_device_id
            and self.policy_version == expected_policy_version
            and self.audience == WORKER_AUDIENCE
        )
        time_matches = valid_now and (self.issued_at - MAX_CLOCK_SKEW <= now < self.expires_at)
        try:
            signature_matches = bool(
                verify_signature(self.canonical_signing_bytes(), self.signature)
            )
        except Exception:
            signature_matches = False
        if not context_matches or not time_matches or not signature_matches:
            raise ValueError("Dispatch authorization failed.")
        try:
            nonce_accepted = bool(accept_nonce(self.nonce))
        except Exception:
            nonce_accepted = False
        if not nonce_accepted:
            raise ValueError("Dispatch authorization failed.")


class EnvironmentResult(_ClosedModel):
    kind: Literal["validate_environment"] = "validate_environment"
    ready: bool
    autocad_connected: bool
    plugin_connected: bool
    publish_enabled: bool
    runtime_series: RuntimeSeries | None = None
    inspection_identity_matched: bool
    error_codes: NoticeCodes = Field(default_factory=list)


class ProjectSummary(_ClosedModel):
    project_id: ProjectId
    alias: SafeAlias
    display_name: SafeDisplay
    enabled: bool = True


class ProjectsResult(_ClosedModel):
    kind: Literal["list_projects"] = "list_projects"
    projects: Annotated[list[ProjectSummary], Field(max_length=100)]


class DrawingSummary(_ClosedModel):
    drawing_id: DrawingId
    display_name: DrawingDisplay
    size_bytes: Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807)]
    modified_utc: datetime

    @field_validator("modified_utc")
    @classmethod
    def _modified_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Drawing timestamp must be UTC and timezone-aware.")
        return value


class DrawingsResult(_ClosedModel):
    kind: Literal["scan_drawings"] = "scan_drawings"
    project_id: ProjectId
    catalog_revision: Sha256
    drawings: Annotated[list[DrawingSummary], Field(max_length=100)]
    next_cursor: CursorId | None = None


class LayoutSummary(_ClosedModel):
    name: SafeDisplay
    model_type: bool
    plotter: SafeDisplay | None = None
    media_name: SafeDisplay | None = None
    plot_style: SafeDisplay | None = None


class PageSetupSummary(_ClosedModel):
    name: SafeDisplay
    model_type: bool
    plotter: SafeDisplay | None = None
    media_name: SafeDisplay | None = None
    plot_style: SafeDisplay | None = None


class FrameSummary(_ClosedModel):
    handle: Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{1,32}$", max_length=32)]
    layer: SafeDisplay
    label: SafeDisplay
    min_point: tuple[float, float, float]
    max_point: tuple[float, float, float]
    confidence: Annotated[float, Field(ge=0, le=1)]


class InspectionResult(_ClosedModel):
    kind: Literal["inspect_drawing"] = "inspect_drawing"
    drawing_id: DrawingId
    layouts: Annotated[list[LayoutSummary], Field(max_length=5_000)]
    page_setups: Annotated[list[PageSetupSummary], Field(max_length=1_000)]
    frames: Annotated[list[FrameSummary], Field(max_length=5_000)]
    notice_codes: NoticeCodes = Field(default_factory=list)


PlanSheetStatus = Literal[
    "matched",
    "unmatched",
    "disallowed_frame_layer",
    "low_confidence",
    "page_setup_mismatch",
    "template_layout_mismatch",
    "template_asset_mismatch",
    "unsupported_scale",
    "layout_conflict",
]


class PlanSheetSummary(_ClosedModel):
    sheet_index: Annotated[int, Field(ge=1, le=5_000)]
    frame_handle: Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{1,32}$", max_length=32)]
    label: SafeDisplay
    status: PlanSheetStatus
    profile_id: SafeAlias | None = None
    target_layout: SafeDisplay
    scale_denominator: Annotated[float, Field(gt=0)] | None = None


class PublishPlanResult(_ClosedModel):
    kind: Literal["create_publish_plan"] = "create_publish_plan"
    plan_schema_version: Literal[1] = 1
    drawing_id: DrawingId
    plan_id: PlanId
    ready: bool
    drawing_sha256: Sha256
    sheets: Annotated[list[PlanSheetSummary], Field(max_length=5_000)]
    notice_codes: NoticeCodes = Field(default_factory=list)


ReadOnlySuccessResult = Annotated[
    EnvironmentResult | ProjectsResult | DrawingsResult | InspectionResult | PublishPlanResult,
    Field(discriminator="kind"),
]

ReadOnlyAction = Literal[
    "validate_environment",
    "list_projects",
    "scan_drawings",
    "inspect_drawing",
    "create_publish_plan",
]


class ErrorResult(_ClosedModel):
    kind: Literal["error"] = "error"
    action: ReadOnlyAction
    code: SafeCode
    retryable: bool
    attention_required: bool = False


WorkerResult = Annotated[
    EnvironmentResult
    | ProjectsResult
    | DrawingsResult
    | InspectionResult
    | PublishPlanResult
    | ErrorResult,
    Field(discriminator="kind"),
]


class WorkerResultEnvelope(_ClosedModel):
    protocol_version: Literal[PROTOCOL_VERSION] = PROTOCOL_VERSION
    tenant_id: TenantId
    user_id: UserId
    device_id: DeviceId
    task_id: TaskId
    operation_id: OperationId
    command_id: CommandId
    completed_at: datetime
    result: WorkerResult
    signature_algorithm: Literal[SIGNATURE_ALGORITHM] = SIGNATURE_ALGORITHM
    signature: Annotated[str, Field(pattern=_SIGNATURE, min_length=86, max_length=86)]

    @field_validator("completed_at")
    @classmethod
    def _completed_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Completion timestamp must be UTC and timezone-aware.")
        return value

    def canonical_signing_bytes(self) -> bytes:
        payload = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


_TASK_ADAPTER = TypeAdapter(WorkerTaskEnvelope)
_RESULT_ADAPTER = TypeAdapter(WorkerResultEnvelope)


def parse_task_payload(payload: bytes | str) -> WorkerTaskEnvelope:
    return _parse_bounded_json(
        payload,
        max_bytes=MAX_TASK_PAYLOAD_BYTES,
        adapter=_TASK_ADAPTER,
        label="Task",
    )


def parse_result_payload(payload: bytes | str) -> WorkerResultEnvelope:
    return _parse_bounded_json(
        payload,
        max_bytes=MAX_RESULT_PAYLOAD_BYTES,
        adapter=_RESULT_ADAPTER,
        label="Result",
    )


def serialize_task_payload(envelope: WorkerTaskEnvelope) -> bytes:
    return _serialize_bounded(envelope, max_bytes=MAX_TASK_PAYLOAD_BYTES, label="Task")


def serialize_result_payload(envelope: WorkerResultEnvelope) -> bytes:
    return _serialize_bounded(envelope, max_bytes=MAX_RESULT_PAYLOAD_BYTES, label="Result")


def _parse_bounded_json(
    payload: bytes | str,
    *,
    max_bytes: int,
    adapter: TypeAdapter[WorkerTaskEnvelope] | TypeAdapter[WorkerResultEnvelope],
    label: str,
) -> WorkerTaskEnvelope | WorkerResultEnvelope:
    try:
        encoded = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} payload must be UTF-8 JSON.") from exc
    if not 2 <= len(encoded) <= max_bytes:
        raise ValueError(f"{label} payload size is invalid.")
    try:
        encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} payload must be UTF-8 JSON.") from exc
    return adapter.validate_json(encoded)


def _serialize_bounded(
    envelope: WorkerTaskEnvelope | WorkerResultEnvelope,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    encoded = envelope.model_dump_json().encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} payload exceeds its safety limit.")
    return encoded
