from cadplot_mcp.backends.autocad_com import _read_labelled_frames, _read_layouts


class FakeLayout:
    Name = "PAFTA-01"
    ModelType = False
    ConfigName = "DWG To PDF.pc3"
    CanonicalMediaName = "ISO_A3_(420.00_x_297.00_MM)"
    StyleSheet = "monochrome.ctb"
    PlotType = 5
    UseStandardScale = True
    StandardScale = 0


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


def test_read_layouts_collects_plot_properties() -> None:
    layouts = _read_layouts(FakeDocument())

    assert len(layouts) == 1
    assert layouts[0].name == "PAFTA-01"
    assert layouts[0].plotter == "DWG To PDF.pc3"
    assert layouts[0].plot_style == "monochrome.ctb"


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
