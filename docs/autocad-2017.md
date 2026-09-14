# AutoCAD 2017: local MCP and PDF publishing

The dedicated `autocad-2017-net46` adapter uses exact `R21.0` loader/runtime
routing and `AutoCAD.Application.21.0` COM selection. It runs the same approved,
copy-only PDF executor, without renaming the 2016 DLL or bypassing version checks.
Autodesk lists 2017 as release 21.0 / .NET Framework 4.6 in its
[compatibility table](https://help.autodesk.com/cloudhelp/2023/ENU/AutoCAD-Customization/files/GUID-A6C680F2-DE2E-418A-A182-E4884073338A.htm).
This targets Windows x64 and licensed full AutoCAD 2017, not LT.

## Installation

On the 2017 workstation, use a clean updated checkout with Git, uv and .NET SDK 8
available. Save drawings and close AutoCAD. At the repository root:

```powershell
git pull --ff-only
.\scripts\install-autocad-2017.ps1
```

The installer finds the standard installation or its registry entry, verifies
all three R21.0 managed references, builds in a fresh directory, verifies the
archive and installs the plug-in in the current user's ApplicationPlugins.
No Autodesk DLL is distributed, SDK downloaded or license accepted; it uses only
the operator's existing authorized installation. It never launches/stops AutoCAD.
Multiple installations require an explicit `-AutoCAD2017SdkDir` selection.
An existing CadPlot bundle is **not overwritten**: preserve it and perform an
operator-reviewed backup/removal first if the installer reports a conflict.

Build-only, optionally with an authorized ObjectARX SDK:

```powershell
.\scripts\build-bundle-2017.ps1 -AutoCAD2017SdkDir "C:\ObjectARX2017\inc"
```

The output is `CadPlotMcp.bundle.zip` plus `bundle-build.json`, containing exact
hashes and SDK identity. A build without Autodesk references is a protocol-only
fixture and cannot be installed as a production plug-in. This standalone package
does not replace the legacy 2016/2025 dual-release kit or its acceptance schema.

## Start and connect

Keep your existing private config with approved drawing roots, workspace and
actual office plot profiles; example page-setup names are not assumed to exist.

```powershell
.\scripts\start-autocad-2017.ps1 -ConfigPath "C:\CadPlot\cadplot-mcp\config.yaml"
```

This explicit launcher validates the config and starts AutoCAD 2017 with
publishing enabled, the configured workspace and pipe `cadplot-mcp-2017`.
Only the child process environment changes, never user/machine environment,
SECURELOAD or TRUSTEDPATHS. Installing alone cannot enable an already-running
AutoCAD session.

Adapt [the local MCP example](../examples/mcp.autocad2017.example.json) to your
checkout/config location and restart the MCP connection. The important values:

```text
CADPLOT_AUTOCAD_PROGID=AutoCAD.Application.21.0
CADPLOT_PIPE_NAME=cadplot-mcp-2017
CADPLOT_CONFIG=<your existing private config.yaml>
```

No Render connection is required. The cloud gateway still does not publish PDFs.

## Target-workstation verification

Call `get_autocad_plugin_status`. Require `connected=true`,
`runtimeSeries=R21.0`, `runtimeSupported=true`, `publishEnabled=true` and
`workspaceConfigured=true`. Then follow the usual approved
`create_publish_plan → preview_publish_plan → stage_publish_job →
queue_publish_job → get_publish_job_status → audit_publish_outputs` workflow.
Page setup, PC3, CTB/STB and media must exist; a blocked plan cannot publish.
Original DWGs remain unchanged. Completion requires the matching receipt,
successful PDF audit and visual review.

Automated routing/protocol tests and compilation do not prove a real AutoCAD
2017 PDF. That acceptance is done on the target workstation, not the development
computer.
