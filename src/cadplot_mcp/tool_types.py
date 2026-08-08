from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field
from typing_extensions import TypedDict

PathString = Annotated[str, Field(min_length=1, max_length=32_767)]
LabelString = Annotated[str, Field(min_length=1, max_length=128)]
PlanIdString = Annotated[
    str,
    Field(min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"),
]
Sha256String = Annotated[
    str,
    Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
JobIdString = Annotated[
    str,
    Field(pattern=r"^job-[0-9]{8}T(?:[0-9]{6}|[0-9]{12})Z-[0-9a-f]{12}$"),
]
TimeoutMilliseconds = Annotated[int, Field(ge=1, le=60_000)]
BatchPageLimit = Annotated[int, Field(ge=1, le=50)]
BatchOffset = Annotated[int, Field(ge=0)]
MaximumFiles = Annotated[int, Field(ge=1, le=50_000)]


class StageApproval(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")

    path: PathString
    plan_id: PlanIdString


class QueueApproval(TypedDict):
    __pydantic_config__ = ConfigDict(extra="forbid")

    manifest_path: PathString
    plan_id: PlanIdString
    manifest_sha256: Sha256String


StageApprovals = Annotated[list[StageApproval], Field(min_length=1, max_length=20)]
QueueApprovals = Annotated[list[QueueApproval], Field(min_length=1, max_length=20)]
