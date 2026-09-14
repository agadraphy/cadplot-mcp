"""Closed translation messages: authorized text, never workstation control or file paths."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

HashId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
OpaqueDrawing = Annotated[
    str, Field(pattern=r"^drw_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
]
OpaqueOperation = Annotated[
    str, Field(pattern=r"^op_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
]
Text = Annotated[str, Field(max_length=32_000)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_:.-]{1,100}$")]
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Count = Annotated[int, Field(ge=0, le=100_000)]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RemoteDecision(Closed):
    span_id: Identifier
    source: Annotated[str, Field(min_length=1, max_length=32_000)]
    source_language: Literal["tr", "en", "other", "uncertain"]
    target: Annotated[str, Field(min_length=1, max_length=32_000)]


class ScanTexts(Closed):
    operation: Literal["scan"] = "scan"
    inventory_id: HashId | None = None
    offset: Count = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class SubmitReview(Closed):
    operation: Literal["review"] = "review"
    inventory_id: HashId
    expected_review_id: HashId | None = None
    decisions: Annotated[list[RemoteDecision], Field(min_length=1, max_length=100)]


class PlanTranslation(Closed):
    operation: Literal["plan"] = "plan"
    inventory_id: HashId
    review_id: HashId
    offset: Count = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class ApplyTranslation(Closed):
    operation: Literal["apply"] = "apply"
    inventory_id: HashId
    review_id: HashId
    approved_plan_id: HashId
    plan_operation_id: OpaqueOperation


TranslationRequest = Annotated[
    ScanTexts | SubmitReview | PlanTranslation | ApplyTranslation, Field(discriminator="operation")
]


class TranslationCommand(Closed):
    action: Literal["translation"] = "translation"
    drawing_id: OpaqueDrawing
    request: TranslationRequest


class Span(Closed):
    id: Identifier
    text: Text
    normalized_for_search: Text


class Record(Closed):
    id: Identifier
    kind: Identifier
    block: Annotated[str, Field(max_length=255)]
    layer: Annotated[str, Field(max_length=255)]
    raw: Text
    spans: Annotated[list[Span], Field(max_length=32_000)]
    writable: bool


class ScanResult(Closed):
    operation: Literal["scan"] = "scan"
    inventory_id: HashId
    total_records: Count
    total_review_spans: Annotated[int, Field(ge=0, le=1_000_000)]
    offset: Count
    next_offset: Count | None
    records: Annotated[list[Record], Field(max_length=100)]
    blocker_count: Count
    blocker_codes: Annotated[list[Code], Field(max_length=100)]


class ReviewResult(Closed):
    operation: Literal["review"] = "review"
    inventory_id: HashId
    review_id: HashId
    reviewed_spans: Count


class Change(Closed):
    record_id: Identifier
    before: Text
    after: Text


class PlanResult(Closed):
    operation: Literal["plan"] = "plan"
    inventory_id: HashId
    review_id: HashId
    plan_id: HashId
    ready: bool
    changed_records: Count
    reviewed_spans: Count
    offset: Count
    next_offset: Count | None
    changes: Annotated[list[Change], Field(max_length=100)]
    blocker_count: Count
    blocker_codes: Annotated[list[Code], Field(max_length=100)]


class ApplyResult(Closed):
    operation: Literal["apply"] = "apply"
    inventory_id: HashId
    review_id: HashId
    plan_id: HashId
    output_id: Annotated[str, Field(pattern=r"^translation-[0-9a-f]{32}$")]
    output_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    changed_records: Count
    source_unchanged: Literal[True]
    text_roundtrip_verified: Literal[True]
    inspected_structure_unchanged: Literal[True]
    visual_review_required: Literal[True] = True
    full_geometry_proven: Literal[False] = False


TranslationResponse = Annotated[
    ScanResult | ReviewResult | PlanResult | ApplyResult, Field(discriminator="operation")
]


class TranslationResult(Closed):
    kind: Literal["translation"] = "translation"
    drawing_id: OpaqueDrawing
    response: TranslationResponse


def require_matching_result(request: TranslationRequest, response: TranslationResponse) -> None:
    if request.operation != response.operation:
        raise ValueError("translation_operation_mismatch")
    if request.inventory_id is not None and request.inventory_id != response.inventory_id:
        raise ValueError("translation_inventory_mismatch")
    if isinstance(request, (ScanTexts, PlanTranslation)) and request.offset != response.offset:
        raise ValueError("translation_page_mismatch")
    if (
        isinstance(request, (PlanTranslation, ApplyTranslation))
        and request.review_id != response.review_id
    ):
        raise ValueError("translation_review_mismatch")
    if isinstance(request, ApplyTranslation) and request.approved_plan_id != response.plan_id:
        raise ValueError("translation_plan_mismatch")
