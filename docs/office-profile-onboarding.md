# Office profile onboarding

Use this process on a licensed company workstation when the exact page setup, PC3, plot style,
canonical media, or title-block layout names are not yet known. It inventories names already
available to the authorized AutoCAD user; it does not export or copy those resources.

## 1. Start with a non-matching inventory profile

Create a new empty pilot root, previewing the exact target first:

```powershell
.\scripts\new-local-pilot.ps1 -DestinationRoot C:\CadPlotPilot -WhatIf
.\scripts\new-local-pilot.ps1 -DestinationRoot C:\CadPlotPilot
```

The setup refuses an existing target and a reparse-point parent. It copies no company asset and
keeps publishing disabled. It places the inventory template at `C:\CadPlotPilot\config.yaml` with
relative, separated `pilot-input` and `pilot-work` roots. If preparing manually, copy
`examples/config.inventory.example.yaml` outside the Git repository and preserve the same
separation. The placeholder profile intentionally cannot match a normal paper label, so
`create_publish_plan` remains blocked.

Keep `CADPLOT_ENABLE_PUBLISH` unset. Start licensed AutoCAD manually, set `CADPLOT_CONFIG` to the
local inventory config, then call:

1. `validate_environment`;
2. `get_autocad_plugin_status` and require `publishEnabled=false`;
3. `inventory_office_resources` on the explicit authorized DWG path.

The tool uses the same read-only inspection path as `inspect_drawing`, but returns a focused,
deduplicated configuration report. Every template layout is marked `candidate_only`; the executor
still requires exactly one floating viewport before it can be accepted.

Record from the structured inspection result:

- detected frame labels, dimensions, handles, and layers;
- paper-space layouts and the approved title-block layout name, if any;
- named page setup;
- exact plotter/PC3 name;
- exact CTB/STB plot-style name;
- exact case-sensitive canonical media name.
- `UseStandardScale`/`StandardScale` or custom numerator/denominator evidence proving the named
  page setup plots paper space at 1:1. Scale-to-fit must remain a blocker.

Do not infer a missing name from a filename or copy company PC3/PMP/CTB/STB/DWT/DWG assets into
the public repository.

## 2. Replace the placeholder locally

For every authorized paper standard, replace the dummy profile with a real profile. Keep aliases
unique and use only names returned by the workstation inspection. Add `canonical_media` for custom
PC3 paper when the office requires exact media validation. Add `template_layout` only when the
source DWG contains the accepted paper-space layout and it has exactly one floating viewport.

Run `match_paper_profile` for every observed label, then `create_publish_plan`. Any unmatched label,
missing/mismatched/non-1:1 page setup, ambiguous frame, nonstandard scale, missing template, or
layout-name collision must remain a blocker.

## 3. Freeze and approve the local profile

Have the authorized CAD reviewer compare the dry-run plan with the office reference. Retain the
approved local config under company access controls and keep it out of Git. Only after the
read-only inventory and dry-run are accepted should the separate staging and licensed one-sheet
publish gates begin.
