from __future__ import annotations

import math
import os
from contextlib import suppress
from pathlib import Path
from threading import Lock
from typing import Any

from cadplot_mcp.models import (
    DrawingInspection,
    FrameCandidate,
    LayoutSummary,
    PageSetupSummary,
)
from cadplot_mcp.paper import PaperSize, parse_paper_size
from cadplot_mcp.security import PathPolicy


class AutoCADUnavailableError(RuntimeError):
    """Raised when read-only AutoCAD inspection cannot be started."""


_COM_LOCK = Lock()


class AutoCADComInspector:
    def __init__(self, path_policy: PathPolicy) -> None:
        self.path_policy = path_policy

    def status(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"available": False, "reason": "AutoCAD COM inspection is Windows-only."}
        try:
            import pythoncom  # type: ignore[import-not-found]
            import win32com.client  # type: ignore[import-not-found]

            pythoncom.CoInitialize()
            try:
                win32com.client.GetActiveObject("AutoCAD.Application")
            finally:
                pythoncom.CoUninitialize()
        except ImportError:
            return {"available": False, "reason": "Install the 'autocad' optional dependency."}
        except Exception as exc:  # pywin32 raises dynamic COM exception types
            return {"available": False, "reason": f"No running AutoCAD instance: {exc}"}
        return {"available": True, "reason": None}

    def inspect_drawing(self, value: str | Path) -> DrawingInspection:
        drawing_path = self.path_policy.require_allowed(value, suffix=".dwg")
        if os.name != "nt":
            raise AutoCADUnavailableError("AutoCAD COM inspection is Windows-only.")

        try:
            import pythoncom  # type: ignore[import-not-found]
            import win32com.client  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AutoCADUnavailableError(
                "pywin32 is required; install CadPlot MCP with the 'autocad' extra."
            ) from exc

        with _COM_LOCK:
            pythoncom.CoInitialize()
            opened_here = False
            document = None
            previous_document = None
            try:
                try:
                    application = win32com.client.GetActiveObject("AutoCAD.Application")
                except Exception as exc:
                    raise AutoCADUnavailableError(
                        "AutoCAD is not running. Start AutoCAD before inspecting a DWG."
                    ) from exc

                previous_document = _safe(application, "ActiveDocument")
                document = _find_open_document(application.Documents, drawing_path)
                if document is None:
                    document = application.Documents.Open(str(drawing_path), True)
                    opened_here = True

                inspection = DrawingInspection(path=str(drawing_path))
                inspection.layouts.extend(_read_layouts(document))
                inspection.page_setups.extend(_read_page_setups(document))
                frames, warnings = _read_labelled_frames(document)
                inspection.frames.extend(frames)
                inspection.warnings.extend(warnings)
                if not inspection.frames:
                    inspection.warnings.append(
                        "No rectangular frame with a paper-size label was found."
                    )
                return inspection
            finally:
                if opened_here and document is not None:
                    with suppress(Exception):
                        document.Close(False)
                if previous_document is not None:
                    with suppress(Exception):
                        previous_document.Activate()
                pythoncom.CoUninitialize()


def _find_open_document(documents: Any, path: Path) -> Any | None:
    expected = os.path.normcase(str(path))
    for document in documents:
        with suppress(Exception):
            if os.path.normcase(str(Path(document.FullName).resolve())) == expected:
                return document
    return None


def _read_layouts(document: Any) -> list[LayoutSummary]:
    layouts: list[LayoutSummary] = []
    for layout in document.Layouts:
        layouts.append(
            LayoutSummary(
                name=_safe(layout, "Name", ""),
                model_type=bool(_safe(layout, "ModelType", False)),
                plotter=_safe(layout, "ConfigName"),
                media_name=_safe(layout, "CanonicalMediaName"),
                plot_style=_safe(layout, "StyleSheet"),
                plot_type=_safe(layout, "PlotType"),
                use_standard_scale=_safe(layout, "UseStandardScale"),
                standard_scale=_safe(layout, "StandardScale"),
            )
        )
    return sorted(layouts, key=lambda item: (item.model_type, item.name.casefold()))


def _read_page_setups(document: Any) -> list[PageSetupSummary]:
    page_setups: list[PageSetupSummary] = []
    for setup in document.PlotConfigurations:
        custom_numerator, custom_denominator = _read_custom_scale(setup)
        page_setups.append(
            PageSetupSummary(
                name=str(_safe(setup, "Name", "")),
                model_type=bool(_safe(setup, "ModelType", False)),
                plotter=_safe(setup, "ConfigName"),
                media_name=_safe(setup, "CanonicalMediaName"),
                plot_style=_safe(setup, "StyleSheet"),
                plot_type=_safe(setup, "PlotType"),
                use_standard_scale=_safe(setup, "UseStandardScale"),
                standard_scale=_safe(setup, "StandardScale"),
                custom_scale_numerator=custom_numerator,
                custom_scale_denominator=custom_denominator,
            )
        )
    return sorted(page_setups, key=lambda item: (item.model_type, item.name.casefold()))


def _read_custom_scale(setup: Any) -> tuple[float | None, float | None]:
    try:
        value = setup.GetCustomScale()
    except Exception:
        return None, None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None, None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None, None


def _read_labelled_frames(document: Any) -> tuple[list[FrameCandidate], list[str]]:
    rectangles: list[tuple[Any, tuple[float, float, float], tuple[float, float, float]]] = []
    block_frames: list[FrameCandidate] = []
    texts: list[tuple[str, tuple[float, float, float]]] = []
    warnings: list[str] = []

    try:
        entities = document.ModelSpace
        for entity in entities:
            object_name = str(_safe(entity, "ObjectName", ""))
            if object_name in {"AcDbText", "AcDbMText"}:
                text = str(_safe(entity, "TextString", ""))
                point = _point3(_safe(entity, "InsertionPoint"))
                if text and point:
                    texts.append((text, point))
            elif object_name == "AcDbPolyline" and bool(_safe(entity, "Closed", False)):
                bounds = _bounds(entity)
                if bounds and _is_axis_aligned_rectangle(entity):
                    rectangles.append((entity, bounds[0], bounds[1]))
            elif object_name == "AcDbBlockReference":
                block_frame, block_warnings = _read_attribute_backed_block_frame(entity)
                warnings.extend(block_warnings)
                if block_frame is not None:
                    block_frames.append(block_frame)
    except Exception as exc:
        warnings.append(f"Model-space entity inspection was incomplete: {exc}")
        return [], warnings

    frames: list[FrameCandidate] = []
    selected_handles: set[str] = set()
    for text, point in texts:
        size = parse_paper_size(text)
        if not size:
            continue
        containing = [
            rectangle
            for rectangle in rectangles
            if _inside(point, rectangle[1], rectangle[2])
        ]
        matching = [
            rectangle
            for rectangle in containing
            if _aspect_ratio_matches(size.width_mm, size.height_mm, rectangle[1], rectangle[2])
        ]
        if not matching:
            if containing:
                warnings.append(
                    f"Label {size.source!r} is inside a rectangle but no frame ratio matches."
                )
            continue
        matching.sort(
            key=lambda item: (
                _rectangle_area(item[1], item[2]),
                str(_safe(item[0], "Handle", "")),
            )
        )
        if len(matching) > 1 and _areas_nearly_equal(matching[0], matching[1]):
            warnings.append(
                f"Label {size.source!r} has multiple equal-size frame candidates; skipped."
            )
            continue
        entity, minimum, maximum = matching[0]
        handle = str(_safe(entity, "Handle", ""))
        if handle in selected_handles:
            continue
        selected_handles.add(handle)
        if len(matching) > 1:
            warnings.append(
                f"Label {size.source!r} has nested frame candidates; selected smallest {handle}."
            )
        frames.append(
            FrameCandidate(
                handle=handle,
                layer=str(_safe(entity, "Layer", "")),
                min_point=minimum,
                max_point=maximum,
                label=size.source,
                width_mm=size.width_mm,
                height_mm=size.height_mm,
                confidence=0.9 if len(matching) == 1 else 0.8,
            )
        )
    for block_frame in block_frames:
        matching_bounds = [
            frame
            for frame in frames
            if _frame_bounds_nearly_equal(frame, block_frame)
            and _same_paper_dimensions(frame, block_frame)
        ]
        duplicate_blocks = [
            frame
            for frame in block_frames
            if frame.handle != block_frame.handle
            and _frame_bounds_nearly_equal(frame, block_frame)
            and _same_paper_dimensions(frame, block_frame)
        ]
        if matching_bounds:
            warnings.append(
                f"Attribute-backed block frame {block_frame.handle} duplicates an existing "
                "frame candidate; skipped."
            )
            continue
        if duplicate_blocks:
            warnings.append(
                f"Attribute-backed block frame {block_frame.handle} has an equal-size "
                "competing block candidate; skipped."
            )
            continue
        frames.append(block_frame)
    return frames, warnings


def _read_attribute_backed_block_frame(
    entity: Any,
) -> tuple[FrameCandidate | None, list[str]]:
    labels: dict[tuple[float, float], PaperSize] = {}
    for text in _block_attribute_texts(entity):
        size = parse_paper_size(text)
        if size is not None:
            labels.setdefault(size.orientation_independent, size)
    if not labels:
        return None, []

    handle = str(_safe(entity, "Handle", ""))
    name = handle or "<no handle>"
    if not handle:
        return None, ["Attribute-backed block frame has no stable handle; skipped."]
    if len(labels) != 1:
        return None, [f"Block {name} contains conflicting paper-size attributes; skipped."]
    if not _is_orthogonal_block_rotation(_safe(entity, "Rotation")):
        return None, [f"Block {name} has a non-orthogonal rotation; skipped."]
    bounds = _bounds(entity)
    if bounds is None:
        return None, [f"Block {name} paper-size attributes were found but bounds are invalid."]

    size = next(iter(labels.values()))
    if not _aspect_ratio_matches(size.width_mm, size.height_mm, bounds[0], bounds[1]):
        return None, [f"Block {name} paper label {size.source!r} does not match its bounds."]
    return (
        FrameCandidate(
            handle=handle,
            layer=str(_safe(entity, "Layer", "")),
            min_point=bounds[0],
            max_point=bounds[1],
            label=size.source,
            width_mm=size.width_mm,
            height_mm=size.height_mm,
            confidence=0.9,
        ),
        [],
    )


def _block_attribute_texts(entity: Any) -> list[str]:
    values: list[str] = []
    for method_name in ("GetAttributes", "GetConstantAttributes"):
        method = _safe(entity, method_name)
        if not callable(method):
            continue
        try:
            attributes = method()
        except Exception:
            continue
        if attributes is None:
            continue
        if not isinstance(attributes, (list, tuple)):
            attributes = (attributes,)
        for attribute in attributes:
            text = str(_safe(attribute, "TextString", "")).strip()
            if text:
                values.append(text)
    return values


def _is_orthogonal_block_rotation(value: Any, tolerance: float = 1e-6) -> bool:
    try:
        rotation = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(rotation):
        return False
    quarter_turns = rotation / (math.pi / 2.0)
    return abs(quarter_turns - round(quarter_turns)) <= tolerance


def _frame_bounds_nearly_equal(
    left: FrameCandidate,
    right: FrameCandidate,
    tolerance: float = 1e-6,
) -> bool:
    values = zip(
        (*left.min_point[:2], *left.max_point[:2]),
        (*right.min_point[:2], *right.max_point[:2]),
        strict=True,
    )
    return all(abs(a - b) <= tolerance * max(1.0, abs(a), abs(b)) for a, b in values)


def _same_paper_dimensions(left: FrameCandidate, right: FrameCandidate) -> bool:
    return tuple(sorted((left.width_mm, left.height_mm))) == tuple(
        sorted((right.width_mm, right.height_mm))
    )


def _rectangle_area(
    minimum: tuple[float, float, float], maximum: tuple[float, float, float]
) -> float:
    return (maximum[0] - minimum[0]) * (maximum[1] - minimum[1])


def _areas_nearly_equal(
    left: tuple[Any, tuple[float, float, float], tuple[float, float, float]],
    right: tuple[Any, tuple[float, float, float], tuple[float, float, float]],
    tolerance: float = 0.01,
) -> bool:
    left_area = _rectangle_area(left[1], left[2])
    right_area = _rectangle_area(right[1], right[2])
    return abs(left_area - right_area) / max(left_area, right_area) <= tolerance


def _safe(value: Any, attribute: str, default: Any = None) -> Any:
    try:
        return getattr(value, attribute)
    except Exception:
        return default


def _point3(value: Any) -> tuple[float, float, float] | None:
    try:
        items = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if len(items) == 2:
        return items[0], items[1], 0.0
    if len(items) >= 3:
        return items[0], items[1], items[2]
    return None


def _bounds(entity: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    try:
        minimum, maximum = entity.GetBoundingBox()
    except Exception:
        return None
    min_point = _point3(minimum)
    max_point = _point3(maximum)
    if not min_point or not max_point:
        return None
    if max_point[0] <= min_point[0] or max_point[1] <= min_point[1]:
        return None
    return min_point, max_point


def _inside(
    point: tuple[float, float, float],
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
) -> bool:
    return minimum[0] <= point[0] <= maximum[0] and minimum[1] <= point[1] <= maximum[1]


def _is_axis_aligned_rectangle(entity: Any, tolerance: float = 1e-6) -> bool:
    try:
        coordinates = tuple(float(value) for value in entity.Coordinates)
    except (AttributeError, TypeError, ValueError):
        return False
    if len(coordinates) != 8:
        return False
    points = list(zip(coordinates[::2], coordinates[1::2], strict=True))
    edges = [
        (points[(index + 1) % 4][0] - point[0], points[(index + 1) % 4][1] - point[1])
        for index, point in enumerate(points)
    ]
    if any(math.hypot(*edge) <= tolerance for edge in edges):
        return False
    for left, right in zip(edges, edges[1:] + edges[:1], strict=True):
        if abs(left[0] * right[0] + left[1] * right[1]) > tolerance:
            return False
    return all(abs(x) <= tolerance or abs(y) <= tolerance for x, y in edges)


def _aspect_ratio_matches(
    paper_width: float,
    paper_height: float,
    minimum: tuple[float, float, float],
    maximum: tuple[float, float, float],
    tolerance: float = 0.02,
) -> bool:
    geometry_width = maximum[0] - minimum[0]
    geometry_height = maximum[1] - minimum[1]
    if min(paper_width, paper_height, geometry_width, geometry_height) <= 0:
        return False
    paper_ratio = max(paper_width, paper_height) / min(paper_width, paper_height)
    geometry_ratio = max(geometry_width, geometry_height) / min(geometry_width, geometry_height)
    return abs(paper_ratio - geometry_ratio) / paper_ratio <= tolerance
