# Page setup and layout validation

Company plotting resources remain local and are referenced by name. CadPlot MCP does not package
or upload DWT, PC3, PMP, CTB/STB, or proprietary title-block files. An explicitly approved external
DWG/DWT may be copied only into a local isolated job workspace under company control.

During read-only inspection the AutoCAD COM adapter lists named `PlotConfigurations`. With
`require_page_setup_match: true`, every matched paper profile must find a named page setup whose:

- name matches the configured `page_setup`;
- setup is for paper space, not model space;
- `PlotType` is exactly `Layout`, rather than window/extents/display/limits;
- plot device matches `plotter`;
- plot style matches `plot_style`;
- canonical media matches `canonical_media` exactly, including case, when configured.
- layout plot scale is verifiably 1:1, either as the standard 1:1 scale or an exact custom 1:1
  numerator/denominator pair; scale-to-fit and unavailable scale evidence are rejected.

A missing setup or mismatched plot type/device/style/media/scale produces `page_setup_mismatch` and
keeps the plan blocked. `canonical_media` is optional because some offices treat the named page setup
as the sole source of media configuration; when supplied, exact comparison is mandatory. The 1:1
layout scale is not optional: the approved viewport carries the model-to-paper scale, so applying
scale-to-fit or another page-setup scale would silently multiply the final drawing scale.

Autodesk references:

- <https://help.autodesk.com/cloudhelp/2024/KOR/AutoCAD-ActiveX-Reference/files/GUID-8C7BADF4-C201-4554-9E23-76DC5A60D787.htm>
- <https://help.autodesk.com/cloudhelp/2024/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_PlotSettingsValidator_SetCanonicalMediaName_PlotSettings_string.html>
- <https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_PlotSettingsValidator_SetPlotConfigurationName_PlotSettings_string_string.html>
- <https://help.autodesk.com/cloudhelp/2025/ENU/AutoCAD-LT-ActiveX-Reference/files/GUID-E8D9D4F5-24C1-4C89-924E-DF57C7F0CF5F.htm>

New layout targets use:

`<layout_prefix>_<four-digit sheet index>_<frame handle>`

The default prefix is `CADPLOT`. If the target already exists in the drawing, the sheet receives
`layout_conflict`. CadPlot MCP does not reuse, rename, or overwrite an existing layout.

## Optional layout template

A paper profile may name `template_layout`. Planning requires that exact layout to exist in the
inspected source DWG and be paper space. During execution, AutoCAD clones it to the unique target
layout. The clone must contain exactly one floating viewport: its paper-space position and size are
preserved while its model target, twist, scale, on/off state, and lock are set from the approved
plan. All title-block and other paper-space geometry stays in the clone.

Zero, multiple, or inspection-unverifiable floating viewports block planning; the runtime repeats
the exact-one check and can produce `template_viewport_count`. No heuristic chooses one. The named
page setup is still applied and independently validated after cloning.

For an external template, configure a read-only `template_roots` boundary plus exact
`template_drawing`, `template_sha256`, and `template_layout` values. The file and its required named
page setup are inspected read-only. The plan ID binds its fingerprint; staging makes a hash-verified
job-local copy and records it in `template_assets`. The plug-in accepts only that direct-child
DWG/DWT copy, replays size/SHA-256, imports the layout with Autodesk's cross-database clone API, and
mangles colliding dependent record names instead of replacing staged drawing definitions. A changed,
escaped, redirected, unused, or metadata-mismatched asset fails closed.
