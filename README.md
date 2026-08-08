# CadPlot MCP

CadPlot MCP is a safety-first MCP server for repeatable AutoCAD plotting workflows.
It is designed for architecture offices that need to inspect many revised drawings,
identify sheet frames, map company page setups, and publish PDFs consistently.

## Current milestone: approval-gated AutoCAD publish candidate

The repository now provides:

- recursive DWG discovery inside explicitly allowed folders;
- read-only layout and plot-setting inspection through a running AutoCAD instance;
- named page-setup inspection with expected PC3 and CTB/STB verification;
- detection of paper-size labels such as `70x100`, `700x1000 mm`, or `50 × 70 cm`;
- configuration-based paper profile matching;
- structured warnings suitable for an approval-first publish plan.
- copy-only staging with a second SHA-256 check in the plug-in;
- an opt-in, bounded queue drained on AutoCAD's main application context;
- in-memory layout/page-setup/viewport creation and one PDF per sheet;
- structural and physical-size PDF auditing.

The executor compiles against an installed AutoCAD 2024 managed API surface. AutoCAD 2016 and
2025 release builds and live plotting still require the matching Autodesk SDK references and a
licensed-workstation acceptance test. Compile-only evidence is not presented as live evidence.

## Safety contract

- No arbitrary AutoLISP or AutoCAD command execution.
- No implicit use of `ActiveDocument` as the target.
- Every DWG path must be inside an allowed root.
- Every approved plan is bound to the source DWG's SHA-256 fingerprint.
- Drawings opened by the inspector are opened read-only and closed without saving.
- Existing open drawings are never closed by the server.
- Existing layouts are never selected as write targets; target-name collisions block the plan.
- Staging copies a DWG into a new isolated job folder and refuses symlink/junction workspaces.
- The plug-in re-hashes the staged DWG immediately before execution.
- Layouts and viewports are execution scaffolding: they are discarded after plotting, keeping the
  staged DWG byte-identical for the final audit.
- Existing PDFs, layouts, or busy plot engines cause refusal; overwrite remains disabled.
- Publish commands are disabled unless the AutoCAD process starts with
  `CADPLOT_ENABLE_PUBLISH=1` and a trusted workspace.
- MCP tool annotations distinguish local write actions from read-only tools; clients must still
  enforce their own approval policy because annotations are hints, not authorization.

## Install

Requirements: Windows, Python 3.11+, and AutoCAD for live DWG inspection.

```powershell
cd cadplot-mcp
uv sync --extra autocad --extra dev
Copy-Item examples/config.example.yaml config.yaml
$env:CADPLOT_CONFIG = "$PWD\config.yaml"
uv run cadplot-mcp
```

Before connecting an MCP client, diagnose the local installation without launching AutoCAD:

```powershell
uv run cadplot-doctor --mode config
uv run cadplot-doctor --mode inspection
uv run cadplot-doctor --mode full
```

`config` checks paths and policy only; `inspection` additionally requires a running AutoCAD COM
session; `full` also requires the installed local named-pipe plug-in and its trusted workspace.
Every mode is read-only and returns machine-readable JSON plus a nonzero exit code when not ready.

AutoCAD must already be running for `inspect_drawing`. The server will not launch it silently.
Close modal AutoCAD dialogs before inspection; COM-level timeouts will be added with the isolated
worker used by the write-capable milestone.

Before AutoCAD testing, run the clearly labelled platform-independent
[synthetic demo](docs/synthetic-demo.md):

```powershell
uv run python scripts/run-synthetic-demo.py
```

## MCP tools

- `validate_environment`: report configuration and AutoCAD connection readiness.
- `get_autocad_plugin_status`: verify the local read-only .NET plug-in bridge.
- `scan_drawings`: find DWG files under an allowed project folder.
- `inspect_drawing`: read layouts, plot properties, and labelled rectangular frames.
- `inventory_office_resources`: produce a read-only exact-name inventory for frame labels, named
  page setups, plotters, plot styles, canonical media, and candidate paper-space layouts without
  approving any mapping.
- `create_publish_plan`: generate a deterministic, hashed dry-run plan with blockers.
- `create_batch_publish_plans`: inspect up to 50 drawings per restartable page while isolating
  per-file blockers and AutoCAD errors.
- `preview_publish_plan`: send only ready, hash-verified plan metadata to the local plug-in;
  it never edits, saves, or plots the drawing.
- `stage_publish_job`: require the exact approved plan ID, re-inspect and re-hash the DWG,
  then create a verified working copy and audit manifest without plotting.
- `stage_publish_batch`: stage at most 20 unique, explicit DWG/plan-ID approvals per call while
  isolating per-file reinspection or approval failures.
- `validate_staged_job`: ask the local plug-in to cross-check the staged manifest against its
  independently configured trusted workspace; it does not queue or plot the job.
- `queue_publish_job`: require the exact staged `plan_id` and `manifest_sha256`, then enqueue the
  byte-bound copy-only job when the installed plug-in has explicitly enabled publishing.
- `queue_publish_batch`: queue at most 20 unique manifest/plan/hash approvals while isolating each
  plug-in refusal or connection error.
- `get_publish_job_status`: report `Pending`, `Running`, `Succeeded`, or `Failed` plus a bounded
  machine-safe failure code.
- `read_publish_receipt`: recover immutable, digest-bound terminal execution evidence from the
  staged job even after AutoCAD has restarted.
- `create_publish_operations_report`: page through up to 50 staged workspace jobs with a stable
  cursor, terminal evidence, output issues, safe next actions, and exact requeue approvals.
- `audit_publish_outputs`: verify job boundaries, staged-DWG integrity, PDF structure, one-page
  count, expected physical paper dimensions, sizes, SHA-256 hashes, and execution evidence without
  changing output. `publish_verified=true` requires both valid PDFs and a successful receipt.
- `match_paper_profile`: map a detected label to a configured office profile.

## Configuration

Copy [examples/config.example.yaml](examples/config.example.yaml). Set `workspace_root` to a local,
dedicated output folder that is not a symlink or junction and does not overlap any `allowed_roots`
source tree. Unknown fields, malformed profile types, empty resource names, non-finite numeric
values, and unsafe tolerance ranges are rejected at startup. Company-owned DWT, CTB/STB,
PC3/PMP, title blocks, and project drawings must not be committed to this repository.
If the exact office resource names are not yet known, follow the
[read-only office profile onboarding](docs/office-profile-onboarding.md) with the deliberately
non-matching inventory config; do not guess production profile values.

A generic local stdio client example is available at
[examples/mcp.local.example.json](examples/mcp.local.example.json). Client configuration formats
vary; see [deployment modes](docs/deployment-modes.md) before connecting a managed ChatGPT
workspace. The [ChatGPT connection architecture](docs/chatgpt-connection.md) separates the
implemented local worker from the still-unimplemented managed HTTPS bridge.
An optional validated Codex plugin wrapper is available under
[`integrations/codex/cadplot-mcp`](integrations/codex/cadplot-mcp/README.md). It invokes an already
installed `cadplot-mcp` CLI and intentionally packages no DWGs, credentials, Autodesk binaries, or
office configuration.

`drawing_unit_mm` declares how many millimetres one model-space unit represents. Frame geometry
and the detected paper label are used to derive rotation and scale. Only values listed under
`scale_denominators` within `scale_tolerance_ratio` are accepted; nonstandard or distorted frames
remain blockers in the dry-run plan.

With `require_page_setup_match: true` (the default), a sheet is ready only when the named page
setup exists and its plotter and plot style match the configured profile. Planned layout names use
`layout_prefix` plus a deterministic index and frame handle; any existing-name collision blocks
the plan instead of overwriting a layout.

For custom PC3 paper definitions, set profile `canonical_media` to the exact value returned by
AutoCAD. The comparison is deliberately case-sensitive. Leave it unset only when the named page
setup is the accepted source of media configuration and the office has approved that policy.
`pdf_page_tolerance_mm` controls the final PDF MediaBox comparison and is capped at 10 mm.
`minimum_frame_confidence` defaults to `0.85`; ambiguous/nested frame detection remains a blocker
below that threshold. See [frame detection](docs/frame-detection.md).
Set optional `frame_layers` when the office has a reliable frame-layer allowlist.

Set optional profile `template_layout` when each source DWG already contains an approved
paper-space title-block layout with exactly one floating viewport. The executor clones that layout,
preserves its paper-space geometry, and retargets the cloned viewport to the approved model window
and scale. Missing/model-space templates or zero/multiple floating viewports block execution.
External DWT/DWG template import is deliberately not inferred from a path or filename.

For large folders, call `create_batch_publish_plans` with the returned `next_offset` until
`has_more=false`. The hard page limit prevents a 300-file run from becoming one fragile, opaque
MCP request.
After staging and approving the returned manifest digests, use `queue_publish_batch` in bounded
pages; do not submit all 300 jobs as one call.
Process-local queue status disappears when AutoCAD exits, but every terminal job writes an
immutable `receipt.json`. Use `read_publish_receipt` or the audit report to resume verification
without guessing from the presence of PDFs alone.
For a large run, call `create_publish_operations_report` until `has_more=false`, passing each
`next_after_job_id` to the next call. Its summary is page-local; retain every `report_page_id` as a
checkpoint. Only items in `awaiting_execution` include a `queue_approval`, and live status must be
checked before submitting it.

## Delivery gates

1. Completed locally: discovery, inspection, deterministic planning, staging, queue protocol,
   executor source, synthetic workflow, and PDF audit.
2. Compile-verified locally: the shared executor against installed AutoCAD 2024 API assemblies.
3. Still required: matching-SDK bundle builds and live acceptance on licensed AutoCAD 2016 and
   2025 with authorized office page setups/plot resources.
4. Later/optional: managed remote MCP bridge for a company ChatGPT workspace.

## AutoCAD plug-in builds

The repository contains separate adapters for AutoCAD 2016 (`net45`, release `R20.1`) and
AutoCAD 2025–2026 (`net8.0-windows`, releases `R25.0`–`R25.1`). A normal solution build validates
the shared protocol without Autodesk binaries. A distributable bundle must be built with local
ObjectARX/AutoCAD managed reference folders:

```powershell
.\scripts\build-bundle.ps1 `
  -AutoCAD2016SdkDir "C:\ObjectARX2016\inc" `
  -AutoCAD2025SdkDir "C:\ObjectARX2025\inc" `
  -DotNet "$env:USERPROFILE\.dotnet\dotnet.exe"
```

The script intentionally fails if the Autodesk reference assemblies are missing. Autodesk SDK
assemblies are development inputs and are not committed or copied into the public bundle.
Before archiving or installing, `scripts/verify-bundle.ps1` requires the exact six-file bundle,
checks both module routes and managed assembly identities, rejects extra files/reparse points, and
prints SHA-256 hashes. The build and install scripts invoke it automatically.

The installer never overwrites an existing bundle. For an upgrade, close AutoCAD, preview the
exact removal with `scripts/uninstall-bundle.ps1 -WhatIf`, run it only after checking the target,
then install the newly verified bundle. The uninstaller rejects junctions and any directory whose
package name/ProductCode does not match CadPlot MCP.

To compile-check the shared executor against a locally installed API without launching AutoCAD:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\probe-autocad-api.ps1 `
  -AutoCADApiDir "C:\Program Files\Autodesk\AutoCAD 2024"
```

This is only an API-signature probe. It does not validate plotting or version compatibility.
See [publish executor](docs/publish-executor.md) and the
[licensed-workstation pilot](docs/monday-pilot.md). A concise Turkish presentation flow is in the
[Monday demo runbook](docs/pazartesi-demo-tr.md).
The final two-version acceptance record is checked by
[`scripts/validate-pilot-evidence.py`](scripts/validate-pilot-evidence.py); its completed company
evidence file stays outside the public repository.

Before launching AutoCAD for staged-job validation, set `CADPLOT_WORKSPACE_ROOT` in the environment
that starts AutoCAD. It must resolve to the same directory as Python configuration
`workspace_root`. The plug-in never accepts a trusted workspace path from an MCP request.
Keep publishing off for inspection and staging validation. Enable it only for the authorized live
pilot by setting `CADPLOT_ENABLE_PUBLISH=1` before starting AutoCAD; restart AutoCAD after changing
either environment variable.

## License

MIT. Autodesk and AutoCAD are trademarks of Autodesk, Inc. This project is not affiliated with
or endorsed by Autodesk.
