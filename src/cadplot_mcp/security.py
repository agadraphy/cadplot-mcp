from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class PathPolicyError(ValueError):
    """Raised when a requested path violates the configured filesystem boundary."""


@dataclass(frozen=True, slots=True)
class PathPolicy:
    allowed_roots: tuple[Path, ...]

    @classmethod
    def from_roots(cls, roots: Iterable[str | Path]) -> PathPolicy:
        resolved = tuple(Path(root).expanduser().resolve() for root in roots)
        if not resolved:
            raise PathPolicyError("At least one allowed root must be configured.")
        return cls(allowed_roots=resolved)

    def require_allowed(
        self,
        value: str | Path,
        *,
        must_exist: bool = True,
        suffix: str | None = None,
    ) -> Path:
        candidate = Path(value).expanduser().resolve(strict=must_exist)
        if not any(candidate == root or root in candidate.parents for root in self.allowed_roots):
            raise PathPolicyError(f"Path is outside configured allowed roots: {candidate}")
        if suffix and candidate.suffix.casefold() != suffix.casefold():
            raise PathPolicyError(f"Expected a {suffix} file: {candidate}")
        return candidate


def require_plain_directory_path(value: str | Path) -> Path:
    """Reject existing symlink/junction components in a writable workspace path."""
    candidate = Path(value).expanduser().absolute()
    existing = candidate
    while not existing.exists():
        if existing.parent == existing:
            break
        existing = existing.parent

    chain = [existing, *reversed(existing.parents)]
    for component in chain:
        if not component.exists():
            continue
        stat = component.lstat()
        attributes = getattr(stat, "st_file_attributes", 0)
        if component.is_symlink() or attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise PathPolicyError(
                f"Writable workspace must not pass through a symlink or junction: {component}"
            )
    return candidate
