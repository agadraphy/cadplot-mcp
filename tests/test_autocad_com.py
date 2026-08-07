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
