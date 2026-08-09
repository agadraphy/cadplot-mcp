import math

from cadplot_mcp.backends.autocad_com import (
    _read_labelled_frames,
    _read_layouts,
    _read_page_setups,
)


class FakeViewport:
    ObjectName = "AcDbViewport"

    def __init__(self, number: int):
        self.Number = number


class FakeLayout:
    Name = "PAFTA-01"
    ModelType = False
    ConfigName = "DWG To PDF.pc3"
    CanonicalMediaName = "ISO_A3_(420.00_x_297.00_MM)"
    StyleSheet = "monochrome.ctb"
    PlotType = 5
    UseStandardScale = True
    StandardScale = 0
    Block = [FakeViewport(1), FakeViewport(2)]


class FakePlotConfiguration:
    Name = "OFFICE_A3"
    ModelType = False
    ConfigName = "DWG To PDF.pc3"
    CanonicalMediaName = "ISO_A3_(420.00_x_297.00_MM)"
    StyleSheet = "monochrome.ctb"
    PlotType = 5
    UseStandardScale = True
    StandardScale = 16

    def GetCustomScale(self):
        return 1.0, 1.0


class FakePolyline:
    ObjectName = "AcDbPolyline"
    Closed = True
    Handle = "AB12"
    Layer = "SHEET"
    Coordinates = (0.0, 0.0, 1000.0, 0.0, 1000.0, 700.0, 0.0, 700.0)

    def GetBoundingBox(self):
        return (0.0, 0.0, 0.0), (1000.0, 700.0, 0.0)


class FakeText:
    ObjectName = "AcDbText"
    TextString = "PAFTA 70x100"
    InsertionPoint = (900.0, 50.0, 0.0)


class FakeDocument:
    Layouts = [FakeLayout()]
    PlotConfigurations = [FakePlotConfiguration()]
    ModelSpace = [FakePolyline(), FakeText()]


class VariablePolyline:
    ObjectName = "AcDbPolyline"
    Closed = True
    Layer = "SHEET"

    def __init__(self, handle: str, minimum: tuple[float, float], maximum: tuple[float, float]):
        self.Handle = handle
        self._minimum = (minimum[0], minimum[1], 0.0)
        self._maximum = (maximum[0], maximum[1], 0.0)
        self.Coordinates = (
            minimum[0],
            minimum[1],
            maximum[0],
            minimum[1],
            maximum[0],
            maximum[1],
            minimum[0],
            maximum[1],
        )

    def GetBoundingBox(self):
        return self._minimum, self._maximum


class VariableText:
    ObjectName = "AcDbText"

    def __init__(self, value: str, point: tuple[float, float]):
        self.TextString = value
        self.InsertionPoint = (point[0], point[1], 0.0)


class VariableDocument:
    Layouts = []

    def __init__(self, entities: list[object]):
        self.ModelSpace = entities


class FakeAttribute:
    def __init__(self, value: str):
        self.TextString = value


class FakeBlockReference:
    ObjectName = "AcDbBlockReference"
    Layer = "SHEET_BLOCK"

    def __init__(
        self,
        handle: str,
        minimum: tuple[float, float],
        maximum: tuple[float, float],
        *,
        attributes: tuple[str, ...] = (),
        constant_attributes: tuple[str, ...] = (),
        rotation: float = 0.0,
    ):
        self.Handle = handle
        self.Rotation = rotation
        self._minimum = (minimum[0], minimum[1], 0.0)
        self._maximum = (maximum[0], maximum[1], 0.0)
        self._attributes = tuple(FakeAttribute(value) for value in attributes)
        self._constant_attributes = tuple(
            FakeAttribute(value) for value in constant_attributes
        )

    def GetAttributes(self):
        return self._attributes

    def GetConstantAttributes(self):
        return self._constant_attributes

    def GetBoundingBox(self):
        return self._minimum, self._maximum


def test_read_layouts_collects_plot_properties() -> None:
    layouts = _read_layouts(FakeDocument())

    assert len(layouts) == 1
    assert layouts[0].name == "PAFTA-01"
    assert layouts[0].plotter == "DWG To PDF.pc3"
    assert layouts[0].plot_style == "monochrome.ctb"
    assert layouts[0].floating_viewport_count == 1


def test_read_page_setups_collects_exact_layout_plot_scale() -> None:
    setups = _read_page_setups(FakeDocument())

    assert len(setups) == 1
    assert setups[0].name == "OFFICE_A3"
    assert setups[0].plot_type == 5
    assert setups[0].use_standard_scale is True
    assert setups[0].standard_scale == 16
    assert setups[0].custom_scale_numerator == 1.0
    assert setups[0].custom_scale_denominator == 1.0


def test_read_labelled_frames_requires_label_inside_closed_polyline() -> None:
    frames, warnings = _read_labelled_frames(FakeDocument())

    assert warnings == []
    assert len(frames) == 1
    assert frames[0].handle == "AB12"
    assert frames[0].label == "70x100"
    assert (frames[0].width_mm, frames[0].height_mm) == (700.0, 1000.0)


def test_nested_frames_select_smallest_valid_candidate_once() -> None:
    outer = VariablePolyline("OUTER", (0, 0), (1000, 700))
    inner = VariablePolyline("INNER", (50, 35), (950, 665))
    text = VariableText("70x100", (900, 100))

    frames, warnings = _read_labelled_frames(VariableDocument([outer, inner, text, text]))

    assert [frame.handle for frame in frames] == ["INNER"]
    assert frames[0].confidence == 0.8
    assert len(warnings) == 1
    assert all("nested frame candidates" in warning for warning in warnings)


def test_equal_size_frame_candidates_are_skipped_as_ambiguous() -> None:
    first = VariablePolyline("FIRST", (0, 0), (1000, 700))
    second = VariablePolyline("SECOND", (0, 0), (1000, 700))
    text = VariableText("70x100", (900, 100))

    frames, warnings = _read_labelled_frames(VariableDocument([first, second, text]))

    assert frames == []
    assert len(warnings) == 1
    assert "multiple equal-size" in warnings[0]


def test_attribute_backed_block_frame_is_detected_without_exploding_block() -> None:
    block = FakeBlockReference(
        "BLOCK1",
        (0, 0),
        (1000, 700),
        constant_attributes=("PAFTA 70x100",),
    )

    frames, warnings = _read_labelled_frames(VariableDocument([block]))

    assert warnings == []
    assert len(frames) == 1
    assert frames[0].handle == "BLOCK1"
    assert frames[0].layer == "SHEET_BLOCK"
    assert frames[0].label == "70x100"
    assert frames[0].confidence == 0.9


def test_orthogonal_quarter_turn_block_frame_is_supported() -> None:
    block = FakeBlockReference(
        "BLOCK90",
        (0, 0),
        (700, 1000),
        attributes=("70x100",),
        rotation=math.pi / 2,
    )

    frames, warnings = _read_labelled_frames(VariableDocument([block]))

    assert warnings == []
    assert [frame.handle for frame in frames] == ["BLOCK90"]


def test_non_orthogonal_attribute_block_is_skipped_fail_closed() -> None:
    block = FakeBlockReference(
        "ROTATED",
        (0, 0),
        (1000, 700),
        attributes=("70x100",),
        rotation=math.pi / 4,
    )

    frames, warnings = _read_labelled_frames(VariableDocument([block]))

    assert frames == []
    assert warnings == ["Block ROTATED has a non-orthogonal rotation; skipped."]


def test_conflicting_attribute_paper_sizes_are_skipped_fail_closed() -> None:
    block = FakeBlockReference(
        "CONFLICT",
        (0, 0),
        (1000, 700),
        attributes=("70x100", "A3"),
    )

    frames, warnings = _read_labelled_frames(VariableDocument([block]))

    assert frames == []
    assert warnings == ["Block CONFLICT contains conflicting paper-size attributes; skipped."]


def test_equal_size_attribute_blocks_are_both_skipped_as_ambiguous() -> None:
    first = FakeBlockReference("BLOCK_A", (0, 0), (1000, 700), attributes=("70x100",))
    second = FakeBlockReference("BLOCK_B", (0, 0), (1000, 700), attributes=("70x100",))

    frames, warnings = _read_labelled_frames(VariableDocument([first, second]))

    assert frames == []
    assert len(warnings) == 2
    assert all("competing block candidate" in warning for warning in warnings)
