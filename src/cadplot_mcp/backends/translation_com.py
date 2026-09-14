"""Explicit-document COM translation. Only whitelisted text setters are permitted."""

from __future__ import annotations

import os
from contextlib import contextmanager, suppress
from pathlib import Path

from cadplot_mcp.backends.autocad_com import (
    _COM_LOCK,
    AutoCADComInspector,
    AutoCADUnavailableError,
    _find_open_document,
    _safe,
)
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.security import PathPolicy
from cadplot_mcp.translation import (
    MAX_ENTITIES,
    MAX_TEXT_CHARS,
    MAX_TEXTS,
    MAX_TOTAL_CHARS,
    TextChange,
    TextRecord,
    TextSnapshot,
    digest,
)

# Observational guards, not setters. Text extents/actual text width are intentionally
# excluded because translation changes them; auto-resizing is never performed.
GUARD_PROPERTIES = (
    "Layer",
    "Linetype",
    "LinetypeScale",
    "Lineweight",
    "Visible",
    "Color",
    "Normal",
    "InsertionPoint",
    "TextAlignmentPoint",
    "Rotation",
    "Height",
    "Width",
    "ScaleFactor",
    "StyleName",
    "Alignment",
    "AttachmentPoint",
    "ObliqueAngle",
    "Thickness",
    "TextHeight",
    "TextWidth",
    "TextRotation",
    "TextStyleName",
    "TextJustify",
    "TextLineSpacingDistance",
    "TextLineSpacingFactor",
    "TextLineSpacingStyle",
    "XScaleFactor",
    "YScaleFactor",
    "ZScaleFactor",
    "Coordinates",
    "StartPoint",
    "EndPoint",
    "Center",
    "Radius",
    "MajorAxis",
    "RadiusRatio",
    "Closed",
    "Elevation",
    "Length",
    "Measurement",
    "TextPosition",
    "DefinitionPoint",
    "ExtLine1Point",
    "ExtLine2Point",
    "TagString",
    "PromptString",
    "Constant",
    "Invisible",
    "Name",
    "MTextBoundaryWidth",
    "TextGenerationFlag",
    "Backward",
    "UpsideDown",
    "DoglegLength",
    "LandingGap",
    "ArrowheadSize",
)
TEXT_KINDS = {"AcDbText", "AcDbMText", "AcDbAttribute", "AcDbAttributeDefinition"}
NON_TEXT_KINDS = {
    "AcDbLine",
    "AcDbPolyline",
    "AcDb2dPolyline",
    "AcDb3dPolyline",
    "AcDbArc",
    "AcDbCircle",
    "AcDbEllipse",
    "AcDbSpline",
    "AcDbHatch",
    "AcDbPoint",
    "AcDbRay",
    "AcDbXline",
    "AcDb3dSolid",
    "AcDbSolid",
    "AcDbTrace",
    "AcDbFace",
    "AcDbViewport",
    "AcDbPViewport",
    "AcDbLeader",
    "AcDbWipeout",
    "AcDbMline",
    "AcDbRegion",
    "AcDbPolyFaceMesh",
    "AcDbPolygonMesh",
    "AcDbBody",
}


def _simple(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list)):
        return [_simple(item) for item in value]
    raise ValueError("Unsupported structural property value.")


def _guard(entity) -> dict:
    values = {"handle": str(entity.Handle), "kind": str(entity.ObjectName)}
    for prop in GUARD_PROPERTIES:
        try:
            value = getattr(entity, prop)
        except Exception:
            continue  # not every entity supports every geometry property
        values[prop] = _simple(value)
    # ActiveX MTEXT Height is character height (unlike .NET MText.Height),
    # so it must remain part of the preservation guard.
    if values["kind"] == "AcDbMLeader":
        count = int(entity.LeaderCount)
        if not 0 <= count <= MAX_ENTITIES:
            raise ValueError("MLeader exceeds the structural audit limit.")
        values["leader_lines"] = [
            {"index": int(line), "vertices": _simple(entity.GetLeaderLineVertices(line))}
            for index in range(count)
            for line in entity.GetLeaderLineIndexes(index)
        ]
    if values["kind"] == "AcDbTable":
        rows, columns = int(entity.Rows), int(entity.Columns)
        if not 0 <= rows * columns <= MAX_TEXTS:
            raise ValueError("Table exceeds the translation safety limit.")
        values["row_heights"] = [float(entity.GetRowHeight(i)) for i in range(rows)]
        values["column_widths"] = [float(entity.GetColumnWidth(i)) for i in range(columns)]
    return values


def collect_snapshot(document) -> TextSnapshot:
    """Enumerate every resident block definition, including model/paper/anonymous blocks.

    Definitions are visited once, not recursively expanded, so repeated inserts and
    cycles cannot omit content or translate shared definitions multiple times.
    """
    records, blockers, structure, visited = [], [], [], set()
    character_count = 0

    def add(entity, block, prop, *, row=-1, column=-1, reason=None):
        nonlocal character_count
        raw = str(entity.GetText(row, column) if prop == "table" else getattr(entity, prop))
        if not raw:
            return
        if len(raw) > MAX_TEXT_CHARS:
            raise ValueError("Text exceeds translation length limit; nothing was truncated.")
        character_count += len(raw)
        if len(records) >= MAX_TEXTS or character_count > MAX_TOTAL_CHARS:
            raise ValueError("Text inventory exceeds safety limit; nothing was truncated.")
        handle = str(entity.Handle)
        key = f"{handle}:{prop}" + (f":{row}:{column}" if prop == "table" else "")
        if "%<" in raw or "\\S" in raw:
            blockers.append(f"field_or_stacked_text_requires_review:{key}")
        records.append(
            TextRecord(
                id=key,
                handle=handle,
                kind=str(entity.ObjectName),
                block=str(block.Name),
                layer=str(entity.Layer),
                property=prop,
                row=row,
                column=column,
                raw=raw,
                writable=reason is None,
                reason=reason,
            )
        )

    def visit(entity, block):
        handle, kind = str(entity.Handle), str(entity.ObjectName)
        if handle in visited:
            return
        visited.add(handle)
        if len(visited) > MAX_ENTITIES:
            raise ValueError("Drawing exceeds entity limit; scan was not silently truncated.")
        guard = _guard(entity)
        structure.append(guard)
        layer = document.Layers.Item(str(entity.Layer))
        reason = "locked_layer" if bool(layer.Lock) else None
        if kind in TEXT_KINDS:
            multiline = kind in {"AcDbAttribute", "AcDbAttributeDefinition"} and bool(
                _safe(entity, "MTextAttribute", False)
            )
            add(
                entity, block, "MTextAttributeContent" if multiline else "TextString", reason=reason
            )
        elif kind == "AcDbMLeader":
            if int(entity.ContentType) == 2:  # acMTextContent
                add(entity, block, "TextString", reason=reason)
            else:
                blockers.append(f"block_mleader_requires_review:{handle}")
        elif "Dimension" in kind:
            add(entity, block, "TextOverride", reason=reason)
        elif kind == "AcDbTable":
            # ActiveX does not expose a reliable content count/complete rich-cell
            # roundtrip here. Inventory visible text, but never flatten a formula,
            # field, merged or multi-content cell through SetText. Fail closed.
            blockers.append(f"table_content_requires_native_review:{handle}")
            for row in range(int(entity.Rows)):
                for col in range(int(entity.Columns)):
                    cell_format = {
                        "row": row,
                        "col": col,
                        "style": str(entity.GetCellTextStyle(row, col)),
                        "height": float(entity.GetCellTextHeight(row, col)),
                        "alignment": int(entity.GetCellAlignment(row, col)),
                        "format": str(entity.GetCellFormat(row, col)),
                    }
                    guard.setdefault("cells", []).append(cell_format)
                    add(
                        entity,
                        block,
                        "table",
                        row=row,
                        column=col,
                        reason="table_write_requires_native_review",
                    )
        elif kind in {"AcDbBlockReference", "AcDbMInsertBlock"}:
            if bool(entity.HasAttributes):
                for attribute in entity.GetAttributes():
                    visit(attribute, block)
                for attribute in entity.GetConstantAttributes():
                    visit(attribute, block)
        elif kind not in NON_TEXT_KINDS:
            blockers.append(f"unsupported_entity:{handle}:{kind}")

    for block in document.Blocks:
        if bool(block.IsXRef):
            blockers.append(f"external_reference:{block.Name}")
            continue
        structure.append({"block": str(block.Name), "handle": str(block.Handle)})
        for entity in block:
            visit(entity, block)
    for layout in document.Layouts:
        structure.append(
            {
                "layout": str(layout.Name),
                "model": bool(layout.ModelType),
                "plotter": str(layout.ConfigName),
                "media": str(layout.CanonicalMediaName),
                "style": str(layout.StyleSheet),
            }
        )
    return TextSnapshot(
        records=sorted(records, key=lambda item: item.id),
        blockers=sorted(set(blockers)),
        entity_count=len(visited),
        structure_sha256=digest(sorted(structure, key=digest)),
    )


@contextmanager
def owned_document(path: Path, policy: PathPolicy, *, readonly: bool, progid: str):
    if os.name != "nt":
        raise AutoCADUnavailableError("DWG translation requires Windows with licensed AutoCAD.")
    import pythoncom
    import win32com.client

    with _COM_LOCK:
        pythoncom.CoInitialize()
        document, previous = None, None
        try:
            application = AutoCADComInspector(policy, progid=progid)._get_active_application(
                win32com.client
            )
            if _find_open_document(application.Documents, path) is not None:
                raise ValueError(
                    "Save and close this DWG before translation; open drawings are refused."
                )
            previous = _safe(application, "ActiveDocument")
            document = application.Documents.Open(str(path), readonly)
            if Path(document.FullName).resolve() != path or (
                not readonly and bool(document.ReadOnly)
            ):
                raise ValueError(
                    "AutoCAD did not open the exact requested drawing in the required mode."
                )
            yield document
        finally:
            try:
                if document is not None:
                    document.Close(False)  # only our document, never the user's open drawing
            finally:
                if previous is not None:
                    with suppress(Exception):
                        previous.Activate()
                pythoncom.CoUninitialize()


def inspect_texts(path: Path, policy: PathPolicy, progid: str) -> TextSnapshot:
    with owned_document(path, policy, readonly=True, progid=progid) as document:
        return collect_snapshot(document)


def apply_to_copy(
    path: Path,
    policy: PathPolicy,
    progid: str,
    *,
    expected_sha256: str,
    expected_snapshot: TextSnapshot,
    changes: list[TextChange],
) -> TextSnapshot:
    if fingerprint_drawing(path, policy)["sha256"] != expected_sha256:
        raise ValueError("Staged source content no longer matches the approved drawing.")
    if os.environ.get("CADPLOT_ENABLE_TRANSLATION") != "1":
        raise ValueError("Translation is not enabled for this MCP process.")
    expected_records = {record.id: record.model_copy() for record in expected_snapshot.records}
    if len({change.record_id for change in changes}) != len(changes):
        raise ValueError("Duplicate text changes are forbidden.")
    for change in changes:
        record = expected_records.get(change.record_id)
        if record is None or not record.writable or record.raw != change.before:
            raise ValueError("Text change is stale, unknown or not writable.")
        record.raw = change.after
    expected_after = expected_snapshot.model_copy(
        update={"records": list(expected_records.values())}
    )
    with owned_document(path, policy, readonly=False, progid=progid) as document:
        if collect_snapshot(document) != expected_snapshot:
            raise ValueError("Copy inventory differs from the approved snapshot.")
        for change in changes:
            record = expected_records[change.record_id]
            entity = document.HandleToObject(record.handle)
            if record.property == "table":
                entity.SetText(record.row, record.column, change.after)
            else:
                setattr(entity, record.property, change.after)
                if record.property == "MTextAttributeContent":
                    entity.UpdateMTextAttribute()
        if collect_snapshot(document) != expected_after:
            raise ValueError(
                "Text/structure guard mismatch; copy not saved. Visual review required."
            )
        document.Save()  # path is a private staged copy, never the source DWG
    reopened = inspect_texts(path, policy, progid)
    if reopened != expected_after:
        raise ValueError("Saved-copy verification failed; output must not be published.")
    return reopened
