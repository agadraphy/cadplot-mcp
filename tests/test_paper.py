import pytest

from cadplot_mcp.paper import parse_paper_size


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("PAFTA 70x100", (700.0, 1000.0)),
        ("500 x 700 mm", (500.0, 700.0)),
        ("50 × 70 cm", (500.0, 700.0)),
        ("ÖZEL 84,1x118,9", (841.0, 1189.0)),
    ],
)
def test_parse_paper_size(label: str, expected: tuple[float, float]) -> None:
    parsed = parse_paper_size(label)

    assert parsed is not None
    assert (parsed.width_mm, parsed.height_mm) == expected


def test_parse_paper_size_rejects_unrelated_text() -> None:
    assert parse_paper_size("DETAY 1/20") is None

