# Page setup and layout validation

Company plotting resources remain local and are referenced by name. CadPlot MCP does not package
or upload DWT, PC3, PMP, CTB/STB, or proprietary title-block files.

During read-only inspection the AutoCAD COM adapter lists named `PlotConfigurations`. With
`require_page_setup_match: true`, every matched paper profile must find a named page setup whose:

- name matches the configured `page_setup`;
- setup is for paper space, not model space;
- plot device matches `plotter`;
- plot style matches `plot_style`;
- canonical media matches `canonical_media` exactly, including case, when configured.

A missing setup or mismatched device/style/media produces `page_setup_mismatch` and keeps the plan
blocked. `canonical_media` is optional because some offices treat the approved named page setup as
the sole source of media configuration; when supplied, exact comparison is mandatory.

Autodesk references:

- <https://help.autodesk.com/cloudhelp/2024/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_PlotSettingsValidator_SetCanonicalMediaName_PlotSettings_string.html>
- <https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_PlotSettingsValidator_SetPlotConfigurationName_PlotSettings_string_string.html>

New layout targets use:

`<layout_prefix>_<four-digit sheet index>_<frame handle>`

The default prefix is `CADPLOT`. If the target already exists in the drawing, the sheet receives
`layout_conflict`. CadPlot MCP does not reuse, rename, or overwrite an existing layout.

## Optional in-drawing layout template

A paper profile may name `template_layout`. Planning requires that exact layout to exist in the
inspected source DWG and be paper space. During execution, AutoCAD clones it to the unique target
layout. The clone must contain exactly one floating viewport: its paper-space position and size are
preserved while its model target, twist, scale, on/off state, and lock are set from the approved
plan. All title-block and other paper-space geometry stays in the clone.

Zero or multiple floating viewports produce `template_viewport_count`; no heuristic chooses one.
The named page setup is still applied and independently validated after cloning. This option does
not import an external DWT/DWG and never searches for a template by filename.
