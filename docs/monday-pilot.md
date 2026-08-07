# Monday Licensed-Workstation Pilot

This pilot proves installation, read-only inspection, approval-gated copy staging, and
MCP-to-AutoCAD validation before any layout mutation or plot command is introduced.

## Inputs to collect

- Licensed AutoCAD release and `ACADVER` value.
- Managed API folder containing `AcMgd.dll` and `AcDbMgd.dll` for that release.
- One anonymized/non-production DWG copy.
- The expected PDF for that DWG.
- Names only for the required PC3/PMP, CTB/STB, page setup, paper, and title-block resources.
- Exact case-sensitive canonical media name reported by AutoCAD for every custom PC3 paper.

Do not copy company drawings or resources to a personal computer without written permission.

## Gate 1: repository checks

```powershell
uv sync --extra autocad --extra dev
uv run ruff check .
uv run pytest
dotnet build src/dotnet/CadPlotMcp.sln --configuration Release
dotnet test src/dotnet/CadPlotMcp.Core.Tests/CadPlotMcp.Core.Tests.csproj --configuration Release
```

## Gate 2: build the real bundle

Run `scripts/build-bundle.ps1` with both Autodesk SDK/reference folders. The script must fail if an
AutoCAD reference is missing. The public repository and release archive must not contain Autodesk
reference assemblies.

## Gate 3: install without overwrite

First preview the copy:

```powershell
.\scripts\install-bundle.ps1 -WhatIf
```

Then run without `-WhatIf`. Start AutoCAD and call MCP tool `get_autocad_plugin_status`. Required
result: `connected=true`, correct adapter/release, and `readOnly=true`.

## Gate 4: drawing inspection

1. Configure an allowed root containing only the approved DWG copy.
2. Configure `workspace_root`. Set `CADPLOT_WORKSPACE_ROOT` to the same directory in the
   environment that launches AutoCAD.
3. Run `scan_drawings` and confirm the exact target path.
4. Run `inspect_drawing` and compare layouts, page setups, plotter, media, style, and frame label.
5. Run `create_publish_plan`; every page setup, scale, and target layout must be ready.

## Gate 5: approved staging validation

1. Record and explicitly approve the current `plan_id`.
2. Call `stage_publish_job` with that exact ID; confirm the original DWG hash is unchanged.
3. Call `validate_staged_job`; require `accepted=true`, `readOnly=true`, and
   `workspaceConfigured=true`.
4. Confirm the job contains a verified source copy, an empty output directory, and manifest only.

## Pilot pass condition

- Source hash unchanged.
- No AutoCAD document saved or closed by the tool.
- Correct layout/frame/profile inventory returned.
- MCP-to-plug-in status round trip works.
- Staged manifest passes the independent plug-in workspace check.
- No proprietary asset is present in the Git repository or release archive.

PDF writing remains out of scope until these gates pass on the licensed workstation.
