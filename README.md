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
- durable, authenticated queue intent: a Windows DPAPI-protected key outside the workspace signs
  exact approvals; never-started work recovers after restart while an interrupted running job is
  held as `job_interrupted` and is never replayed automatically;
- durable pending cancellation: an exact signed tombstone survives restart, while a running
  PlotEngine operation is never force-aborted;
- in-memory layout/page-setup/viewport creation and one PDF per sheet;
- structural and physical-size PDF auditing.
- deterministic CycloneDX 1.7 SBOM generation bound to the exact commit, lock, runtime dependency
  inventory, wheel/source archive, and release DLL hashes, with no workstation paths or Autodesk
  binaries.

The executor compiles against an installed AutoCAD 2024 managed API surface. AutoCAD 2016 and
2025 release builds and live plotting still require the matching Autodesk SDK references and a
licensed-workstation acceptance test. Compile-only evidence is not presented as live evidence.
The repository and kit verifiers also do not claim Authenticode signing; an organization-owned
signing certificate and timestamp policy remain an external release gate. See the
[software bill of materials](docs/software-bill-of-materials.md).
The [completion audit](docs/completion-audit.md) maps every project requirement to its current
authoritative evidence and keeps licensed/company-only gates explicit.
At load time the plug-in normalizes the real `ACADVER` value and refuses to enable publishing when
the loaded adapter does not match the running AutoCAD release.

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
- Every sheet plots to an owned temporary PDF; final names appear only after every plot completes
  and the staged DWG closes without saving. A later promotion failure rolls back earlier unchanged
  outputs by length and SHA-256 when the filesystem permits it.
- Existing PDFs, layouts, or busy plot engines cause refusal; overwrite remains disabled.
- Publish commands are disabled unless the AutoCAD process starts with
  `CADPLOT_ENABLE_PUBLISH=1` and a trusted workspace.
- Queue acceptance is acknowledged only after an immutable job-local intent is persisted. A
  separate started marker prevents ambiguous crash recovery from silently plotting twice.
- Pending cancellation requires the exact plan and manifest digests, persists before memory state
  changes, and is idempotent. `Running` or terminal work cannot be cancelled in place.
- All MCP tools expose closed top-level structured-output schemas; plan and receipt identities also
  carry exact digest patterns. Successful schema-v2 receipts bind the ordered PDF output set by
  sheet index, filename, byte length, and SHA-256. The real STDIO smoke test exercises this contract
  with `call_tool`.
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

For an administrator-authorized Secure MCP Tunnel developer pilot, run the same tool surface on a
loopback-only Streamable HTTP endpoint:

```powershell
uv run cadplot-mcp-http --port 8765
```

This binds only `127.0.0.1` at `/mcp`, enforces exact Host/Origin guards, and caps requests at
1 MiB. It is not an authenticated public server and must not be forwarded with a generic tunnel or
reverse proxy. See [loopback Streamable HTTP transport](docs/loopback-http.md).

For the recommended private ChatGPT pilot target over STDIO, generate a secret-free administrator
handoff report without contacting OpenAI or launching AutoCAD:

```powershell
uv run cadplot-tunnel-preflight --transport stdio --probe-target `
  --output C:\CadPlotPilot\chatgpt\tunnel-preflight.json
```

See [Secure MCP Tunnel administrator handoff](docs/secure-tunnel-handoff.md). Platform tunnel
creation, runtime credentials, workspace permissions, and the live app scan remain administrator
gates.

After those external gates pass, prepare the hash-bound 13-case ChatGPT tool-selection evaluation:

```powershell
uv run cadplot-chatgpt-eval prepare `
  --preflight C:\CadPlotPilot\chatgpt\tunnel-preflight.json `
  --output-dir C:\CadPlotPilot\chatgpt\evaluation-001
```

The sanitized validator tests direct, indirect, follow-up, approval, adversarial, edge,
cancellation, and recovery behavior without retaining company paths or raw chat content. It proves
the ChatGPT tool contract only and cannot replace licensed AutoCAD 2016/2025 evidence. See
[ChatGPT tool-selection evaluation](docs/chatgpt-evaluation.md).

Before connecting an MCP client, diagnose the local installation without launching AutoCAD:

```powershell
uv run cadplot-doctor --mode config
uv run cadplot-doctor --mode inspection
uv run cadplot-doctor --mode full
```

`config` checks paths and policy only; `inspection` additionally requires a running AutoCAD COM
session; `full` also requires the installed local named-pipe plug-in and its trusted workspace.
Every mode is read-only and returns machine-readable JSON plus a nonzero exit code when not ready.

For a first office inventory, create a new empty local pilot folder without overwriting anything:

```powershell
.\scripts\new-local-pilot.ps1 -DestinationRoot C:\CadPlotPilot -WhatIf
.\scripts\new-local-pilot.ps1 -DestinationRoot C:\CadPlotPilot
```

The script copies only the public non-matching inventory config and creates empty `pilot-input` and
`pilot-work` folders. It never copies company assets or enables publishing.

AutoCAD must already be running for `inspect_drawing`. The server will not launch it silently.
Each drawing inspection runs in a separate helper process with the bounded
`inspection_timeout_seconds` deadline (default 120). A hung COM call becomes an isolated file error
instead of freezing the MCP server or batch page. See
[inspection isolation](docs/inspection-isolation.md).

The local Python client and AutoCAD plug-in use the current-user pipe `cadplot-mcp` by default.
When licensed AutoCAD 2016 and 2025 instances must run at the same time, start each AutoCAD process
and its corresponding MCP server with the same distinct safe `CADPLOT_PIPE_NAME`, for example
`cadplot-mcp-2016` and `cadplot-mcp-2025`. Also select read-only COM inspection with
`CADPLOT_AUTOCAD_PROGID=AutoCAD.Application.20.1` for 2016 or
`AutoCAD.Application.25.0` for 2025. CadPlot verifies the returned application version and refuses
ambiguous/foreign ProgIDs. Safe pipe names match `[A-Za-z0-9._-]{1,128}`; invalid names fail closed
on both sides. Never point one MCP process at an unverified AutoCAD instance.

Before AutoCAD testing, run the clearly labelled platform-independent
[synthetic demo](docs/synthetic-demo.md):

```powershell
uv run python scripts/run-synthetic-demo.py
```

## MCP tools

- `validate_environment`: report configuration and AutoCAD connection readiness.
- `get_autocad_plugin_status`: verify the local read-only .NET plug-in bridge. When publishing is
  enabled it also reports `queueCapacity`, `queuePending`, `queueRunning`, `queueAvailable`, and the
  exact `queueAuthentication` scheme so large-run clients can apply backpressure without trusting
  an unsigned restart queue.
- `scan_drawings`: find DWG files under an allowed project folder.
- `inspect_drawing`: read layouts, plot properties, labelled rectangular polylines, and strictly
  validated orthogonal block frames backed by instance attributes or bounded read-only nested
  definition text; the detector never explodes a block.
- `inventory_office_resources`: produce a read-only exact-name inventory for frame labels, named
  page setups, plotters, plot styles, canonical media, and candidate paper-space layouts without
  approving any mapping.
- `create_publish_plan`: generate a deterministic, hashed dry-run plan with blockers.
- `create_batch_publish_plans`: inspect up to 50 drawings per restartable page while isolating
  per-file blockers and AutoCAD errors; subsequent pages require the first page's exact
  metadata-bound inventory ID.
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
- `cancel_publish_job`: durably cancel only the exact `Pending` plan/manifest identity. The signed
  cancellation survives restart; the tool never interrupts a `Running` AutoCAD plot.
- `queue_publish_batch`: queue at most 20 unique manifest/plan/hash approvals while isolating each
  plug-in refusal or connection error. Schema v2 distinguishes retryable `deferred` items from
  permanent `failed` items and stops issuing pipe requests after the first `queue_full` response.
- `get_publish_job_status`: report `Pending`, `Running`, `Succeeded`, `Failed`, or `Cancelled` plus a bounded
  machine-safe failure code.
- `get_publish_batch_status`: read up to 20 exact plan IDs in one bounded MCP call, summarize live
  states, and take one final internally consistent queue-capacity sample without writing files.
- `read_publish_receipt`: recover immutable, manifest-and-output-digest-bound terminal execution
  evidence from the staged job even after AutoCAD has restarted.
- `create_publish_operations_report`: page through up to 50 staged workspace jobs with a stable
  cursor, terminal evidence, output issues, safe next actions, and exact requeue approvals.
- `audit_publish_outputs`: verify job boundaries, staged-DWG integrity, PDF structure, one-page
  count, expected physical paper dimensions, sizes, SHA-256 hashes, and execution evidence without
  changing output. `publish_verified=true` requires valid PDFs and a successful receipt whose
  canonical output-set binding independently revalidates.
- `match_paper_profile`: map a detected label to a configured office profile.

## Configuration

Copy [examples/config.example.yaml](examples/config.example.yaml). Set `workspace_root` to a local,
dedicated output folder that is not a symlink or junction and does not overlap any `allowed_roots`
source tree. Unknown fields, malformed profile types, empty resource names, non-finite numeric
values, and unsafe tolerance ranges are rejected at startup. Company-owned DWT, CTB/STB,
PC3/PMP, title blocks, and project drawings must not be committed to this repository.
Run `uv run python scripts/audit-source-tree.py` before publication; CI and local preflight reject
tracked/non-ignored CAD assets, plot resources, archives, local config, and high-confidence secrets.
The same audit requires immutable full-SHA GitHub Action references, exact read-only workflow
permissions, non-persistent checkout credentials, and no `pull_request_target`; Dependabot is
configured for `uv`, NuGet, and Actions updates.
CI runs the complete Python suite on 3.11, 3.12, and 3.13, then runs the heavier canonical
release/MCP/.NET preflight once on 3.12. Full-SHA pinning alone is insufficient: the source audit
also rejects action repositories outside the explicit reviewed allowlist.
For a network-backed, lock-exact dependency check, run
`scripts/run-local-preflight.ps1 -AuditDependencies`. It audits the exported production Python lock
with hashes, all transitive .NET packages, and the Python license declarations. The resulting JSON
is bound to `uv.lock`; unknown licenses or known vulnerabilities fail the gate. Results are a
point-in-time database check, not a permanent security guarantee, and they do not launch AutoCAD.
If the exact office resource names are not yet known, follow the
[read-only office profile onboarding](docs/office-profile-onboarding.md) with the deliberately
non-matching inventory config; do not guess production profile values.

A generic local stdio client example is available at
[examples/mcp.local.example.json](examples/mcp.local.example.json). Client configuration formats
vary; see [deployment modes](docs/deployment-modes.md) before connecting a managed ChatGPT
workspace. The [ChatGPT connection architecture](docs/chatgpt-connection.md) separates the
implemented local worker/loopback tunnel target from the still-unimplemented managed HTTPS bridge.
An optional validated Codex plugin wrapper is available under
[`integrations/codex/cadplot-mcp`](integrations/codex/cadplot-mcp/README.md). It invokes an already
installed `cadplot-mcp` CLI and intentionally packages no DWGs, credentials, Autodesk binaries, or
office configuration.

`drawing_unit_mm` declares how many millimetres one model-space unit represents. Frame geometry
and the detected paper label are used to derive rotation and scale. Only values listed under
`scale_denominators` within `scale_tolerance_ratio` are accepted; nonstandard or distorted frames
remain blockers in the dry-run plan.

With `require_page_setup_match: true` (the default), a sheet is ready only when the named page
setup exists, its plot type is exactly `Layout`, its plotter and plot style match the configured
profile, and its paper-space plot scale is verifiably 1:1. Scale-to-fit is rejected because
viewport scale already carries the approved model-to-paper ratio. Planned layout names use
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

An approved external DWG/DWT can be used only through the explicit three-part contract:
top-level `template_roots`, profile `template_drawing`, and the reviewed exact
`template_sha256`, together with `template_layout`. Planning inspects that asset read-only and binds
its layout/page setup/fingerprint into the plan ID. Staging re-hashes it and copies it under the
isolated job; the plug-in imports only that staged copy, re-hashes it again, and discards all imported
scaffolding with the staged DWG after plotting. No filename search or implicit office-library trust
is performed.

For large folders, retain the first `create_batch_publish_plans` result's `inventory_id`, then pass
it as `expected_inventory_id` with each returned `next_offset` until `has_more=false`. Pagination
fails closed if the DWG inventory changes. The hard page limit prevents a 300-file run from becoming
one fragile, opaque MCP request.
After staging and approving the returned manifest digests, use `queue_publish_batch` in bounded
pages; do not submit all 300 jobs as one call.
Read `queueAvailable` from `get_autocad_plugin_status` or the most recent queue/status response,
then retry the unchanged exact approvals reported as `deferred` when slots reopen. A `queue_full`
result is backpressure, not a publish failure and not permission to alter or silently replace an
approval. The MCP server instructions direct capable clients to keep feeding an already approved
large run autonomously; the operator still retains AutoCAD's publish opt-in gate.
Use `get_publish_batch_status` for bounded live polling instead of issuing one MCP call per plan;
its queue telemetry is sampled once after the per-job reads and is not claimed to be a simultaneous
snapshot of every job transition.
Restart state is reconstructed only from authenticated durable intent and immutable terminal
`receipt.json` evidence. Never-started pending work is restored; started work without a receipt is
held as `job_interrupted` and is not replayed. Use `read_publish_receipt` or the audit report to
resume verification without guessing from PDFs alone. Copying the workspace to another Windows
user or machine does not transfer pending authorization because the DPAPI key remains user-bound.
If a wrong profile or batch scope is discovered, call `cancel_publish_job` with the unchanged exact
plan and manifest digest for each still-pending item. Retry is idempotent. A job already reported as
`Running` is deliberately not aborted; wait for terminal evidence and review its outputs.
For a large run, call `create_publish_operations_report` until `has_more=false`, passing each
`next_after_job_id` to the next call. Its summary is page-local; retain every `report_page_id` as a
checkpoint. Only items in `awaiting_execution` include a `queue_approval`, and live status must be
checked before submitting it. A structurally valid cancellation marker is reported as
`cancelled_hold` without requeue approval; only the live plug-in authenticates it as `Cancelled`.

The local preflight also runs an explicit 300-drawing synthetic scale rehearsal:

```powershell
uv run python scripts/run-synthetic-batch-demo.py --drawings 300
```

It creates 300 non-DWG synthetic fixtures in a temporary directory, plans them in 15 immutable
pages, stages 300 independently hash-bound copies in 15 approval batches, saturates a synthetic
seven-slot queue and retries only the exact deferred approvals, walks the restart report without
repeats, reads all 300 plan identities through 15 bounded batch-status calls, and structurally
audits 300 generated PDFs. It deliberately creates no plug-in
receipt, so all 300 outputs remain `manual_review`, `execution_verified=0`, and
`publish_verified=0`. This proves bounded local orchestration and fail-closed recovery at the target
count; it is not AutoCAD execution evidence.

## Delivery gates

1. Completed locally: discovery, inspection, deterministic planning, staging, queue protocol,
   executor source, synthetic workflow, and PDF audit.
2. Compile-verified locally: the shared executor against installed AutoCAD 2024 API assemblies.
3. Still required: matching-SDK bundle builds and live acceptance on licensed AutoCAD 2016 and
   2025 with authorized office page setups/plot resources.
4. Implemented locally: loopback Streamable HTTP tunnel target with real protocol/header smoke.
5. Still external/optional: Secure MCP Tunnel admin provisioning or a managed authenticated remote
   gateway for a company ChatGPT workspace.

## AutoCAD plug-in builds

The repository contains separate adapters for AutoCAD 2016 (`net45`, release `R20.1`) and
AutoCAD 2025 (`net8.0-windows`, release `R25.0`). A normal solution build validates
the shared protocol without Autodesk binaries. A distributable bundle must be built with local
ObjectARX/AutoCAD managed reference folders:

```powershell
.\scripts\build-bundle.ps1 `
  -AutoCAD2016SdkDir "C:\ObjectARX2016\inc" `
  -AutoCAD2025SdkDir "C:\ObjectARX2025\inc" `
  -DotNet "$env:USERPROFILE\.dotnet\dotnet.exe"
```

The script intentionally fails if the Autodesk reference assemblies are missing. Autodesk SDK
assemblies are development inputs and are not committed or copied into the public bundle. Before
compilation, all three managed assemblies must expose one consistent release series and the build
requires exactly `R20.1` for the 2016 adapter and `R25.0` for the 2025 adapter; a folder from another
installed AutoCAD release is rejected even when it contains the same DLL filenames.

An authorized operator can run the rehearsal, dependency audit, both exact-SDK builds, bundle
verification, combined release-kit build, and final verification with one no-overwrite command:

```powershell
.\scripts\build-complete-release.ps1 `
  -AutoCAD2016SdkDir "C:\ObjectARX2016\inc" `
  -AutoCAD2025SdkDir "C:\ObjectARX2025\inc" `
  -DotNet "$env:USERPROFILE\.dotnet\dotnet.exe"
```

See [Autodesk SDK prerequisites](docs/autodesk-sdk-prerequisites.md). The SDK agreement remains an
external operator gate; the project does not download, install, accept, or redistribute Autodesk
development files.

A real build also requires a clean Git worktree and never replaces an earlier artifact. Its default
output is `artifacts/cadplot-bundle-<version>-<commit>/`, containing the extracted bundle, bundle
ZIP, and `bundle-build.json`. That manifest binds the exact commit, package version, redacted SDK
assembly identities/hashes, verified bundle file hashes, and archive hash while retaining
`autocad_launched=false` and `live_publish_proven=false`. `verify-bundle-release.ps1` independently
checks the directory, manifest, ZIP entry set, and every inner file hash without extracting it.
After the clean matching-SDK bundle and a readiness report exist for the same commit,
`scripts/build-release-kit.ps1` creates a no-overwrite transfer root containing that full bundle
release, the readiness-bound Python wheel, lock data, a `git archive` source snapshot, safe install
scripts, the inventory-only config, pilot evidence commands, and operator runbooks. The kit carries
its own hash-bound `verify-release-kit.ps1`, so verification and pilot collection need no separate
source checkout. The verifier checks both
manifests, the exact tree, embedded bundle/API evidence, current lock-bound Python/.NET vulnerability
evidence, the complete Python license inventory, and every outer ZIP entry without extracting it.
See [verified release-kit installation](docs/release-kit-install.md). The kit keeps
`licensed_live_pilot_ready=false`, `public_release_ready=false`, and `live_publish_proven=false`;
only separately retained licensed 2016/2025 pilot evidence can change those claims.
Its `install-release-kit.ps1` provides a single `-WhatIf`-capable, resumable first-install command:
it reuses only verified matching components, prepares pilot/Python first, and exposes the bundle last
without launching AutoCAD, changing global PATH, or enabling publish.
Bundle mutations refuse to run while `acad.exe` is active. A successful orchestrated install retains
a no-overwrite local receipt binding the release manifest, Python manifest, bundle hashes, and paths.
The included read-only `verify-release-install.ps1` independently replays that complete evidence
chain later; expected inventory-config edits are reported without weakening immutable hash checks.
`uninstall-release-kit.ps1` provides a receipt-bound, `-WhatIf`-capable and resumable rollback: it
removes the verified bundle before Python while deliberately preserving pilot data and the receipt.
The kit's `install-python.ps1` first revalidates the whole transfer, installs exact frozen/hash-
required dependencies plus the wheel into a unique staging venv, and atomically names a version/
commit target without changing global PATH. `verify-python-install.ps1` rechecks its retained wheel,
lock, requirements digest, command surface, and installed distribution inventory.
`uninstall-python.ps1` revalidates that exact version/commit environment, previews with `-WhatIf`,
and removes only an atomically renamed quarantine; modified or redirected directories are preserved.
The smaller local demo builder creates a no-overwrite delivery directory containing the exact kit,
its ZIP, and an outer `demo-kit-build.json`. The embedded `verify-demo-archive.ps1` checks the ZIP
without extraction, including safe entry names, exact membership, per-entry hashes, inner-manifest
identity, and false live-evidence flags. `verify-demo-kit.ps1` independently verifies the flat kit,
which omits machine-local API directory paths from its portable manifest.
Before archiving or installing, `scripts/verify-bundle.ps1` requires the exact six-file bundle,
checks both module routes and managed assembly identities, rejects extra files/reparse points, and
prints SHA-256 hashes. The build and install scripts invoke it automatically.

Installation copies into a uniquely named non-loadable staging directory, verifies every copied
file hash against the already verified source, and only then atomically renames it to
`CadPlotMcp.bundle`. The verifier rejects redirected files as well as redirected directories.
If copying or hash verification fails, the uniquely named non-loadable staging directory is
retained for explicit inspection instead of being recursively deleted by the installer.
`scripts/run-local-preflight.ps1` exercises `-WhatIf`, installation, copied-hash verification,
`-WhatIf` removal, and removal with a clearly labelled protocol-only fixture; this smoke never
launches AutoCAD and is not a matching-SDK or live-publish result.

The installer never overwrites an existing bundle. For an upgrade, close AutoCAD, preview the
exact removal with `scripts/uninstall-bundle.ps1 -WhatIf`, run it only after checking the target,
then pass the new build JSON's `bundle` path explicitly as `install-bundle.ps1 -SourceBundle ...`.
The uninstaller rejects junctions and any directory whose
package name/ProductCode does not match CadPlot MCP. It also requires the exact verified bundle
contents and hashes twice, atomically renames the exact target to a unique non-`.bundle` quarantine,
and recursively removes only that checked quarantine. A rename/removal failure preserves evidence.

To compile-check the shared executor against a locally installed API without launching AutoCAD:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\probe-autocad-api.ps1 `
  -AutoCADApiDir "C:\Program Files\Autodesk\AutoCAD 2024"
```

The probe derives the managed target from the assembly identities (`R20.1 → net45`, `R25.0 →
net8.0-windows`) instead of compiling every release through `net48`; unknown series are rejected.

This is only an API-signature probe. It does not validate plotting or version compatibility.
See [publish executor](docs/publish-executor.md) and the
[licensed-workstation pilot](docs/monday-pilot.md). A concise Turkish presentation flow is in the
[Monday demo runbook](docs/pazartesi-demo-tr.md).
The final two-version acceptance record is checked by the installed `cadplot-validate-pilot`
command (with [`scripts/validate-pilot-evidence.py`](scripts/validate-pilot-evidence.py) retained as
a source-checkout wrapper); its completed company
evidence file stays outside the public repository. Live status exposes the embedded build commit
and running adapter DLL SHA-256. The pilot assembler derives the release commit from the verified
`bundle-build.json`, re-hashes every bundle ZIP entry, and refuses either version when its running
binary does not match the corresponding adapter artifact. Pilot schema v6 also requires the exact
authenticated durable-queue scheme and derives a
path-redacted external-template record from each immutable job manifest and requires the approved,
post-pilot company-source, and staged-copy hashes to remain identical. Each run also binds the
authorized one-page reference PDF and published output by path-redacted SHA-256/size/page geometry,
and independently revalidates the schema-v2 receipt's exact output count and output-set digest,
rejects a reference outside allowed roots or with different orientation/size during collection and
later validation, and requires seven separate visual-check attestations instead of one blanket switch.
After both runs pass, the installed `cadplot-acceptance` finalizer binds that evidence to the exact
release-kit directory/ZIP, wheel, bundle, build manifest, commit, and package version. Its sanitized
report excludes company-run details and keeps `public_release_ready=false` until separate company
publication and maintainer release approvals are explicit; see
[release acceptance](docs/release-acceptance.md).

Before launching AutoCAD for staged-job validation, set `CADPLOT_WORKSPACE_ROOT` in the environment
that starts AutoCAD. It must resolve to the same directory as Python configuration
`workspace_root`. The plug-in never accepts a trusted workspace path from an MCP request.
Keep publishing off for inspection and staging validation. Enable it only for the authorized live
pilot by setting `CADPLOT_ENABLE_PUBLISH=1` before starting AutoCAD; restart AutoCAD after changing
either environment variable.

## License

MIT. Autodesk and AutoCAD are trademarks of Autodesk, Inc. This project is not affiliated with
or endorsed by Autodesk.
