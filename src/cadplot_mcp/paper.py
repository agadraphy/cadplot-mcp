from __future__ import annotations

import re
from dataclasses import dataclass

PAPER_SIZE_PATTERN = re.compile(
    r"(?<!\d)(?P<width>\d{2,4}(?:[.,]\d+)?)\s*[xX×]\s*"
    r"(?P<height>\d{2,4}(?:[.,]\d+)?)(?:\s*(?P<unit>mm|cm))?(?!\d)",
    re.IGNORECASE,
)
A_SERIES_PATTERN = re.compile(r"(?<![A-Z0-9])A(?P<size>[0-5])(?![A-Z0-9])", re.IGNORECASE)
A_SERIES_MM = {
    "0": (841.0, 1189.0),
    "1": (594.0, 841.0),
    "2": (420.0, 594.0),
    "3": (297.0, 420.0),
    "4": (210.0, 297.0),
    "5": (148.0, 210.0),
}


@dataclass(frozen=True, slots=True)
class PaperSize:
    width_mm: float
    height_mm: float
    source: str

    @property
    def orientation_independent(self) -> tuple[float, float]:
        return tuple(sorted((self.width_mm, self.height_mm)))


def parse_paper_size(text: str) -> PaperSize | None:
    match = PAPER_SIZE_PATTERN.search(text)
    if not match:
        series_match = A_SERIES_PATTERN.search(text)
        if not series_match:
            return None
        width, height = A_SERIES_MM[series_match.group("size")]
        return PaperSize(width_mm=width, height_mm=height, source=series_match.group(0))

    width = float(match.group("width").replace(",", "."))
    height = float(match.group("height").replace(",", "."))
    unit = (match.group("unit") or "").casefold()
    if unit == "cm" or (not unit and max(width, height) <= 200):
        width *= 10
        height *= 10
    return PaperSize(width_mm=width, height_mm=height, source=match.group(0))


def normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())
