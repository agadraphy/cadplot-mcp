# CadPlot MCP

CadPlot MCP is a safety-first MCP server for repeatable AutoCAD plotting workflows.
It is designed for architecture offices that need to inspect many revised drawings,
identify sheet frames, map company page setups, and publish PDFs consistently.

## Current milestone: read-only inspector and publish preview

The first milestone intentionally cannot modify or plot a drawing. It provides:

- recursive DWG discovery inside explicitly allowed folders;
- read-only layout and plot-setting inspection through a running AutoCAD instance;
- detection of paper-size labels such as `70x100`, `700x1000 mm`, or `50 × 70 cm`;
- configuration-based paper profile matching;
- structured warnings suitable for an approval-first publish plan.

Write and publish actions will only be added after the inspection and dry-run contract is stable.

## Safety contract

- No arbitrary AutoLISP or AutoCAD command execution.
- No implicit use of `ActiveDocument` as the target.
- Every DWG path must be inside an allowed root.
- Every approved plan is bound to the source DWG's SHA-256 fingerprint.
- Drawings opened by the inspector are opened read-only and closed without saving.
- Existing open drawings are never closed by the server.
- Staging copies a DWG into a new isolated job folder and refuses symlink/junction workspaces.
- Overwrite and original-file modification will remain disabled by default.

## Install

Requirements: Windows, Python 3.11+, and AutoCAD for live DWG inspection.

```powershell
git clone https://github.com/YOUR-USER/cadplot-mcp.git
cd cadplot-mcp
uv sync --extra autocad --extra dev
Copy-Item examples/config.example.yaml config.yaml
$env:CADPLOT_CONFIG = "$PWD\config.yaml"
uv run cadplot-mcp
```

AutoCAD must already be running for `inspect_drawing`. The server will not launch it silently.
Close modal AutoCAD dialogs before inspection; COM-level timeouts will be added with the isolated
worker used by the write-capable milestone.

## MCP tools

- `validate_environment`: report configuration and AutoCAD connection readiness.
- `get_autocad_plugin_status`: verify the local read-only .NET plug-in bridge.
- `scan_drawings`: find DWG files under an allowed project folder.
- `inspect_drawing`: read layouts, plot properties, and labelled rectangular frames.
- `create_publish_plan`: generate a deterministic, hashed dry-run plan with blockers.
- `preview_publish_plan`: send only ready, hash-verified plan metadata to the local plug-in;
  it never edits, saves, or plots the drawing.
- `stage_publish_job`: require the exact approved plan ID, re-inspect and re-hash the DWG,
  then create a verified working copy and audit manifest without plotting.
- `audit_publish_outputs`: verify job boundaries, staged-DWG integrity, expected PDF headers,
  sizes, and SHA-256 hashes without changing any output.
- `match_paper_profile`: map a detected label to a configured office profile.

## Configuration

Copy [examples/config.example.yaml](examples/config.example.yaml). Set `workspace_root` to a local,
dedicated output folder that is not a symlink or junction. Company-owned DWT, CTB/STB,
PC3/PMP, title blocks, and project drawings must not be committed to this repository.

## Roadmap

1. Read-only discovery and inspection.
2. Deterministic dry-run publish plans with warnings and previews.
3. In-process AutoCAD .NET worker for layout/page-setup operations.
4. Copy-only PDF publishing with output validation and an audit report.
5. Optional remote MCP bridge for managed ChatGPT workspaces.

## AutoCAD plug-in builds

The repository contains separate adapters for AutoCAD 2016 (`net45`, release `R20.1`) and
AutoCAD 2025–2026 (`net8.0-windows`, releases `R25.0`–`R25.1`). A normal solution build validates
the shared protocol without Autodesk binaries. A distributable bundle must be built with local
ObjectARX/AutoCAD managed reference folders:

```powershell
.\scripts\build-bundle.ps1 `
  -AutoCAD2016SdkDir "C:\ObjectARX2016\inc" `
  -AutoCAD2025SdkDir "C:\ObjectARX2025\inc" `
  -DotNet "C:\Users\YOUR-USER\.dotnet\dotnet.exe"
```

The script intentionally fails if the Autodesk reference assemblies are missing. Autodesk SDK
assemblies are development inputs and are not committed or copied into the public bundle.

The shared .NET core also contains a bounded, trusted-workspace publish queue. It is deliberately
not exposed through the named pipe yet; an AutoCAD-version adapter must execute queued work on the
supported application context and pass licensed-workstation tests first.

## License

MIT. Autodesk and AutoCAD are trademarks of Autodesk, Inc. This project is not affiliated with
or endorsed by Autodesk.
