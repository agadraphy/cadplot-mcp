from __future__ import annotations

import pytest

from cadplot_gateway.redaction import UnsafePublicPayload, assert_public_payload_safe


def test_accepts_opaque_public_payload() -> None:
    assert_public_payload_safe(
        {
            "operation_id": "8f2b2d24-70be-4e25-83ab-6aacb6f92c32",
            "state": "SUCCEEDED",
            "drawings": [{"drawing_id": "70912127-c383-4db3-b0c0-9b7b61497253"}],
        }
    )


@pytest.mark.parametrize(
    "value",
    [
        {"path": "hidden"},
        {"message": r"C:\\SECRET-CANARY\\drawing.dwg"},
        {"message": "C:relative.dwg"},
        {"message": r"\\server\\share\\drawing.dwg"},
        {"message": "folder/drawing.dwg"},
        {"message": "..%2fdrawing.dwg"},
        {"message": r"\\.\\pipe\\cadplot-mcp"},
        {"message": "CADPLOT_CONFIG was invalid"},
        {"message": "AutoCAD.Application.25.0"},
        {"message": "file:///private/drawing.dwg"},
    ],
)
def test_rejects_local_control_or_path_data(value: object) -> None:
    with pytest.raises(UnsafePublicPayload):
        assert_public_payload_safe(value)
