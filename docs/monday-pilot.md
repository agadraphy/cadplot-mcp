# Monday Licensed-Workstation Pilot

This pilot first proves installation, inspection, and staging with publishing disabled. It then
enables one explicitly approved sheet and proves the real in-memory layout/viewport/PDF path.

## Inputs to collect

- Licensed AutoCAD release and `ACADVER` value.
- Managed API folder containing `AcMgd.dll`, `AcDbMgd.dll`, and `AcCoreMgd.dll` for that release.
- One anonymized/non-production DWG copy.
- The expected PDF for that DWG.
- Names only for the required PC3/PMP, CTB/STB, page setup, paper, and title-block resources.
- Exact case-sensitive canonical media name reported by AutoCAD for every custom PC3 paper.

Do not copy company drawings or resources to a personal computer without written permission.

## Gate 1: repository checks

Run the fail-fast local preflight. It does not launch AutoCAD and therefore does not prove live
publishing:

```powershell
.\scripts\run-demo-rehearsal.ps1 `
  -AutoCADApiDir "C:\Program Files\Autodesk\AutoCAD 2025" `
  -WriteReport
```

If the installed SDK is outside `PATH`, pass `-DotNet` explicitly. The script runs the locked
environment sync, lint, Python tests, synthetic demo, Python package/audit, .NET build/tests, and
an isolated wheel-install MCP smoke plus the optional compile-only API probe. It also requires a
stable clean commit, binds the wheel hash, and writes a non-overwriting report to the returned
`report_path`. Its final JSON must show `passed=true`, `local_demo_ready=true`,
`autocad_launched=false`, and `live_publish_proven=false`.

Equivalent individual commands are:

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
result: `connected=true`, correct adapter/release, `readOnly=true`, and `publishEnabled=false`.
The product field must include the live `ACADVER`; record it with the pilot evidence.

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

## Gate 6: one-sheet write pilot

1. Close AutoCAD. In the same launcher environment set `CADPLOT_ENABLE_PUBLISH=1`; keep the same
   `CADPLOT_WORKSPACE_ROOT`, then restart AutoCAD.
2. Require `get_autocad_plugin_status` to report `publishEnabled=true`.
3. Use a one-sheet anonymized DWG copy first. Record source and staged SHA-256 values.
4. Call `queue_publish_job` with the exact manifest path, approved `plan_id`, and approved
   `manifest_sha256` returned by staging.
5. Poll `get_publish_job_status`; require `Succeeded`. A failure code is evidence to diagnose, not
   permission to overwrite or bypass a gate.
6. Call `read_publish_receipt`; require a digest-bound `succeeded` terminal receipt. Restart
   AutoCAD once and confirm the same receipt can still be read.
7. Call `audit_publish_outputs`; require `publish_verified=true` plus one valid, unencrypted,
   one-page PDF with the expected physical paper dimensions.
8. Require both source and staged DWG hashes to remain unchanged.
9. Visually compare orientation, crop, viewport scale, lineweights, plot style, text/font output,
   and title block against the office reference PDF.

Repeat Gate 6 separately on licensed AutoCAD 2016 and 2025. Do not infer one from the other.
Record both runs using the [licensed pilot evidence contract](pilot-evidence.md) and require its
collector/assembler/validator chain to return `valid=true`.

## Gate 7: bounded batch recovery

1. Stage and queue a small authorized batch before increasing volume.
2. Restart AutoCAD after terminal receipts exist.
3. Page `create_publish_operations_report` using `next_after_job_id` until `has_more=false`.
4. Require completed jobs to remain `complete`; require every other item to expose an explicit
   safe next action. Never requeue `manual_review` or `failed` jobs in place.
5. Retain each `report_page_id` in the pilot evidence.

## Pilot pass condition

- Source hash unchanged.
- No AutoCAD document saved or closed by the tool.
- Correct layout/frame/profile inventory returned.
- MCP-to-plug-in status round trip works.
- Staged manifest passes the independent plug-in workspace check.
- The queued job succeeds, its immutable receipt validates after restart, and the PDF audit reports
  `publish_verified=true`.
- Source and staged DWG hashes remain unchanged after plotting.
- The authorized visual comparison is accepted for scale, crop, style, and orientation.
- No proprietary asset is present in the Git repository or release archive.

Production rollout remains blocked until every gate passes and the office accepts the sample PDF.
