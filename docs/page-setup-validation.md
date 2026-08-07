# Page setup and layout validation

Company plotting resources remain local and are referenced by name. CadPlot MCP does not package
or upload DWT, PC3, PMP, CTB/STB, or proprietary title-block files.

During read-only inspection the AutoCAD COM adapter lists named `PlotConfigurations`. With
`require_page_setup_match: true`, every matched paper profile must find a named page setup whose:

- name matches the configured `page_setup`;
- plot device matches `plotter`;
- plot style matches `plot_style`.

A missing setup or mismatched device/style produces `page_setup_mismatch` and keeps the plan
blocked. Canonical media-name validation will be added with the licensed AutoCAD plot adapter,
because Autodesk documents canonical media names as case-sensitive.

New layout targets use:

`<layout_prefix>_<four-digit sheet index>_<frame handle>`

The default prefix is `CADPLOT`. If the target already exists in the drawing, the sheet receives
`layout_conflict`. CadPlot MCP does not reuse, rename, or overwrite an existing layout.
