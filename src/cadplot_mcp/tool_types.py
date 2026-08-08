from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field
from typing_extensions import TypedDict

PathString = Annotated[
    str,
    Field(
        min_length=1,
        max_length=32_767,
        description="Explicit local path; it must pass CadPlot's configured path policy.",
    ),
]
LabelString = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        description="Paper-size label observed in or expected from a drawing frame.",
    ),
]
PlanIdString = Annotated[
    str,
    Field(
        min_length=71,
        max_length=71,
        pattern=r"^sha256:[0-9a-f]{64}$",
        description=(
            "Exact plan identifier returned by planning; write tools require explicit approval "
            "of this unchanged value."
        ),
    ),
]
Sha256String = Annotated[
    str,
    Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
        description="Exact lowercase SHA-256 digest returned by the preceding trusted step.",
    ),
]
JobIdString = Annotated[
    str,
    Field(
        pattern=r"^job-[0-9]{8}T(?:[0-9]{6}|[0-9]{12})Z-[0-9a-f]{12}$",
        description="Stable CadPlot job cursor returned by an earlier operations-report page.",
    ),
]
TimeoutMilliseconds = Annotated[
    int,
    Field(ge=1, le=60_000, description="Bounded local plug-in request timeout in milliseconds."),
]
BatchPageLimit = Annotated[
    int,
    Field(ge=1, le=50, description="Bounded number of jobs or drawings returned in one page."),
]
BatchOffset = Annotated[
    int,
    Field(ge=0, description="Zero-based drawing offset returned by the previous batch page."),
]
MaximumFiles = Annotated[
    int,
    Field(ge=1, le=50_000, description="Safety cap for DWG discovery before pagination."),
]


class StageApproval(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")

    path: PathString
    plan_id: PlanIdString


class QueueApproval(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")

    manifest_path: PathString
    plan_id: PlanIdString
    manifest_sha256: Sha256String


StageApprovals = Annotated[
    list[StageApproval],
    Field(
        min_length=1,
        max_length=20,
        description="One to twenty unique explicit DWG path and exact plan-ID approvals.",
    ),
]
QueueApprovals = Annotated[
    list[QueueApproval],
    Field(
        min_length=1,
        max_length=20,
        description=(
            "One to twenty unique manifest path, exact plan-ID, and exact manifest-hash approvals."
        ),
    ),
]
