# Verified Release Kit Installation

This procedure installs a release kit that was built from one clean Git commit with real AutoCAD
2016 (`R20.1`) and AutoCAD 2025 (`R25.0`) managed API references. It does not prove a live publish.
Keep AutoCAD closed until the bundle copy and environment configuration are complete.

## 1. Verify before installing

The transferred kit contains its own hash-bound verifier. Run it directly from the kit before any
installation; no source checkout is required:

```powershell
$releaseRoot = "C:\CadPlotTransfer\cadplot-release-kit-0.1.0-abcdef0"
& "$releaseRoot\CadPlotMcp.release\scripts\verify-release-kit.ps1" `
  -ReleaseRoot $releaseRoot
```

The verifier checks the exact directory set, every SHA-256 digest, the embedded bundle build/API
identity, and every ZIP entry without extracting the archive. Compare the separately transferred
`release-kit-build.json` digest/commit through the company's trusted handoff channel; a verifier
inside the same package proves integrity consistency, not publisher authenticity. Do not install a
kit that fails.

For the normal first installation, the included resumable orchestrator performs this verification,
creates/reuses the pilot workspace, installs/reuses the exact Python environment, and makes the
AutoCAD bundle visible last. Preview all three destinations first:

```powershell
$preview = & "$releaseRoot\CadPlotMcp.release\scripts\install-release-kit.ps1" `
  -ReleaseRoot $releaseRoot `
  -PilotRoot C:\CadPlotPilot `
  -WhatIf `
  -PassThru
$install = & "$releaseRoot\CadPlotMcp.release\scripts\install-release-kit.ps1" `
  -ReleaseRoot $releaseRoot `
  -PilotRoot C:\CadPlotPilot `
  -PassThru
```

The command never launches AutoCAD, changes global PATH, or enables publish. If an earlier attempt
stopped, rerun it: an existing component is reused only after exact release hash/commit or required
pilot-structure verification; a conflicting component fails closed. Required tools and every child
installer's discoverable checks run before the first mutation. The separate steps below remain
available for review, upgrades, and recovery.
It also writes a no-overwrite, exact commit/hash-bound local receipt under
`C:\CadPlotPilot\install-receipts`; a resume verifies and reuses that receipt instead of replacing it.
The receipt's canonical payload digest covers every evidence field. It contains local installation
paths and must stay with the workstation evidence, not in the public repository. All bundle mutation
commands fail before changes while any `acad.exe` is running.

Verify the installed state independently at any later time without rerunning the installer:

```powershell
$installed = & "$releaseRoot\CadPlotMcp.release\scripts\verify-release-install.ps1" `
  -ReleaseRoot $releaseRoot `
  -ReceiptPath $install.InstallReceipt `
  -PassThru
$installed | Format-List
```

This read-only check binds the receipt to the transferred kit, installed bundle hashes, installed
Python manifest/distribution inventory, and exact pilot paths. It never starts AutoCAD or enables
publishing. `ConfigChangedSinceInstall=true` is informational because the placeholder inventory is
expected to be edited after installation; the verifier still validates the path and all immutable
installation evidence. Compare `ReceiptSha256` through a trusted handoff if the receipt is used as
formal evidence: its embedded digest proves consistency, not signer identity.

For a complete rollback, keep AutoCAD closed and preview the receipt-bound removal first:

```powershell
$removePreview = & "$releaseRoot\CadPlotMcp.release\scripts\uninstall-release-kit.ps1" `
  -ReleaseRoot $releaseRoot `
  -ReceiptPath $install.InstallReceipt `
  -WhatIf `
  -PassThru
$remove = & "$releaseRoot\CadPlotMcp.release\scripts\uninstall-release-kit.ps1" `
  -ReleaseRoot $releaseRoot `
  -ReceiptPath $install.InstallReceipt `
  -PassThru
```

The orchestrator independently verifies the receipt and every component, removes the host-loadable
bundle first, then removes the exact Python environment. It deliberately preserves the pilot root,
local config, authorized input, isolated work, and install receipt. If a run stops after the bundle
removal, rerun the same command: an absent component is accepted only through the same verified
receipt and any remaining component is fully reverified before removal. The command is idempotent
after both components are absent. It never stops AutoCAD itself.

## 2. Install the AutoCAD bundle

Preview the exact destination first, then repeat without `-WhatIf`:

```powershell
$kit = "C:\CadPlotTransfer\cadplot-release-kit-0.1.0-abcdef0\CadPlotMcp.release"
.\scripts\install-bundle.ps1 -SourceBundle "$kit\autocad\CadPlotMcp.bundle" -WhatIf
.\scripts\install-bundle.ps1 -SourceBundle "$kit\autocad\CadPlotMcp.bundle"
```

The installer never overwrites an existing `CadPlotMcp.bundle`. Use the verified uninstaller with
AutoCAD closed before an upgrade. The source must be the exact `CadPlotMcp.bundle` child of the
verified release root; the installer rechecks the sibling archive, `bundle-build.json`, exact
commit, and matching-SDK evidence before any copy. The protocol-only allowance is reserved for
repository smoke fixtures and must never be used for a workstation install.

```powershell
& "$kit\scripts\uninstall-bundle.ps1" -WhatIf
& "$kit\scripts\uninstall-bundle.ps1"
```

The uninstaller validates package name/ProductCode, exact contents, reparse points, and file hashes
twice. It then atomically renames only `CadPlotMcp.bundle` to a unique non-`.bundle` quarantine and
recursively removes only that checked path. If AutoCAD holds the bundle open or removal fails, the
operation fails closed and preserves either the install or quarantine for inspection.

## 3. Install the Python MCP command

Preview the version/commit-bound destination, then install the exact wheel and every production
dependency from the embedded frozen lock with mandatory hashes:

```powershell
& "$kit\scripts\install-python.ps1" -ReleaseRoot $releaseRoot -WhatIf
$pythonInstall = & "$kit\scripts\install-python.ps1" `
  -ReleaseRoot $releaseRoot `
  -PassThru
& "$kit\scripts\verify-python-install.ps1" `
  -InstallRoot $pythonInstall.Target
$commandRoot = $pythonInstall.CommandRoot
& "$commandRoot\cadplot-doctor.cmd" --help
& "$commandRoot\cadplot-collect-pilot.cmd" --help
& "$commandRoot\cadplot-collect-recovery.cmd" --help
& "$commandRoot\cadplot-assemble-pilot.cmd" --help
& "$commandRoot\cadplot-validate-pilot.cmd" --help
& "$commandRoot\cadplot-acceptance.cmd" --help
& "$commandRoot\cadplot-tunnel-preflight.cmd" --help
```

For a Secure MCP Tunnel operator session, prefix only the current process PATH so the official
tunnel client resolves this exact verified installation; do not mutate the machine-wide PATH:

```powershell
$env:PATH = "$commandRoot;$env:PATH"
cadplot-tunnel-preflight --transport stdio --probe-target
```

The installer first re-runs the embedded release-kit verifier, exports dependencies from the exact
`uv.lock`, requires every package hash, installs the wheel with `--no-deps`, runs `uv pip check`,
records the exact installed distribution inventory, and atomically renames a unique staging
environment. It never overwrites an existing version/commit target. A failed staging directory is
retained for inspection instead of being recursively deleted. Use the full paths under the returned
`bin`; the relative launchers remain valid after atomic staging rename. The installer does not
mutate global PATH or another Python environment.

The included `pyproject.toml`, `uv.lock`, and commit-bound source ZIP are retained for dependency
review and reproducible maintenance. Both kit manifests retain the current, lock-bound Python/.NET
vulnerability result and transitive Python license inventory. This is point-in-time scan evidence,
not a permanent security guarantee or legal advice. The wheel contains no Autodesk/company assets.

For an upgrade or removal, preview and then remove only the returned version/commit installation:

```powershell
& "$kit\scripts\uninstall-python.ps1" -InstallRoot $pythonInstall.Target -WhatIf
& "$kit\scripts\uninstall-python.ps1" -InstallRoot $pythonInstall.Target
```

The uninstaller verifies the complete environment twice, rejects redirected or modified paths, then
atomically renames only that exact install to a unique non-loadable quarantine before recursive
removal using Windows long-path-safe directory handling. A rename or removal failure retains the
quarantine for inspection. It never removes the destination parent or an unverified directory.

## 4. Create the external pilot workspace

Preview and then create an empty, no-overwrite workspace outside the release kit:

```powershell
& "$kit\scripts\new-local-pilot.ps1" -DestinationRoot C:\CadPlotPilot -WhatIf
& "$kit\scripts\new-local-pilot.ps1" -DestinationRoot C:\CadPlotPilot
cadplot-doctor --config C:\CadPlotPilot\config.yaml --mode config
```

The script works both from a source checkout and from the transferred release kit. It creates only
an empty `pilot-input`, an isolated `pilot-work`, and a deliberately non-matching inventory config.

## 5. Add the authorized office profile

Copy `config/config.inventory.example.yaml` outside the release kit and replace its deliberately
non-matching placeholders only with values inventoried on the licensed company workstation. Do not
copy company DWG, PC3, PMP, CTB/STB, DWT, credentials, or local `config.yaml` into the repository or
release kit.

Set `CADPLOT_CONFIG` to that external file. Keep `CADPLOT_ENABLE_PUBLISH` unset during inspection and
dry-run. Follow `docs/pazartesi-demo-tr.md` for the licensed one-sheet acceptance flow. After both
licensed runs and both per-release small-batch recovery checks, use the installed
`cadplot-collect-pilot`, `cadplot-collect-recovery`, `cadplot-assemble-pilot`, and
`cadplot-validate-pilot` commands documented in `docs/pilot-evidence.md`; no repository Python
environment is required. After both one-sheet runs and both recovery records validate, follow
`docs/release-acceptance.md` to bind the pilot evidence to this exact transferred kit without
exposing company-run details.

Before the one-sheet write gate, start only the selected licensed AutoCAD release with the exact
environment and run the bundled read-only preflight. For AutoCAD 2025:

```powershell
$env:CADPLOT_CONFIG = $installed.Config
$env:CADPLOT_WORKSPACE_ROOT = Join-Path $installed.PilotRoot "pilot-work"
$env:CADPLOT_AUTOCAD_PROGID = "AutoCAD.Application.25.0"
$env:CADPLOT_PIPE_NAME = "cadplot-mcp-2025"
Remove-Item Env:CADPLOT_ENABLE_PUBLISH -ErrorAction SilentlyContinue
# Start licensed AutoCAD 2025 from this environment, then:
& "$releaseRoot\CadPlotMcp.release\scripts\test-licensed-workstation.ps1" `
  -ReleaseRoot $releaseRoot `
  -ReceiptPath $installed.ReceiptPath `
  -AutoCADRelease 2025 `
  -OutputPath (Join-Path $installed.PilotRoot "licensed-preflight-2025.json")
```

For 2016 use `AutoCAD.Application.20.1`, `cadplot-mcp-2016`, release `2016`, and a separate output.
The command runs the install verifier both before and after full doctor inspection, compares the live
adapter DLL hash and embedded commit to the verified installation, and refuses a publish-enabled or
cross-version process. It never launches AutoCAD, opens a DWG, initializes the publish queue, or proves
a PDF. On success it also creates `licensed-preflight-2025.mcp.json` beside the evidence record. This
no-overwrite file contains one exact standard `"mcpServers"` entry bound to the verified installed
Python, config, workspace, ProgID, and pipe; the read-only entry deliberately omits
`CADPLOT_ENABLE_PUBLISH`. For 2016 the default name is `licensed-preflight-2016.mcp.json`. Preserve
each release's record and generated config with the private pilot evidence. Before writing the record,
the verifier runs the installed `cadplot-probe-client-config` against that exact generated entry. It
requires MCP protocol `2025-11-25`, the exact 20-tool surface, and zero CadPlot tool calls; it does not
launch AutoCAD, open a DWG, or prove a PDF.

The bounded protocol check can be repeated manually without calling any CadPlot tool:

```powershell
$commandRoot = Split-Path -Parent $installed.PythonExecutable
& "$commandRoot\cadplot-probe-client-config.cmd" --help
& "$commandRoot\cadplot-probe-client-config.cmd" `
  (Join-Path $installed.PilotRoot "licensed-preflight-2025.mcp.json") `
  --server-id cadplot-2025-readonly --expect-mode readonly
```

After inspection/staging approval, close AutoCAD, set `CADPLOT_ENABLE_PUBLISH=1` in the same exact
launcher environment, restart that release, and bind the authenticated write-capable session to the
read-only record before queueing anything:

```powershell
$env:CADPLOT_ENABLE_PUBLISH = "1"
# Restart licensed AutoCAD 2025 from this environment, then:
& "$releaseRoot\CadPlotMcp.release\scripts\test-licensed-workstation.ps1" `
  -ReleaseRoot $releaseRoot `
  -ReceiptPath $installed.ReceiptPath `
  -AutoCADRelease 2025 `
  -SessionMode Publish `
  -ReadOnlyPreflightPath (Join-Path $installed.PilotRoot "licensed-preflight-2025.json") `
  -OutputPath (Join-Path $installed.PilotRoot "licensed-publish-session-2025.json")
```

The publish-session gate requires the status command's `readOnly=true`, session capability
`publishEnabled=true`, exact queue authentication, and the same config/receipt/adapter binary evidence.
It reads the prior preflight as a stable bounded
snapshot and rechecks it after doctor inspection. It does not open, stage, queue, or plot a DWG;
`live_publish_proven=false` and `licensed_live_pilot_ready=false` remain mandatory.

Only after that command succeeds, use the generated
`licensed-publish-session-2025.mcp.json`. Copy or merge its single `"mcpServers"` child into the
approved ChatGPT/Codex desktop client configuration; do not replace unrelated client entries. The
publish entry contains exact `CADPLOT_ENABLE_PUBLISH=1` because the authenticated live session was
verified. Use the read-only `.mcp.json` for inspection-only work. `-McpConfigOutputPath <path>` can
select another output under the verified pilot root, but neither the evidence file nor the MCP config
is ever overwritten. Restart the client after importing the selected entry. Never import a publish
config produced by a failed, interrupted, copied, or different-release verification attempt. The
publish record's `mcp_tool_surface_sha256` must match the prior read-only record and both must record
`mcp_tools_called=false`.

## Evidence boundary

Successful kit verification means the two installers and their source are cryptographically bound
to one commit. It does not mean AutoCAD was launched or PDF publishing was proven. Record AutoCAD
2016 and 2025 live evidence separately before any production-ready claim.

## Queue authentication state

The first publish-enabled AutoCAD start creates a random queue-authentication key under the current
Windows user's local `CadPlotMcp\state` directory. Its bytes are protected with Windows DPAPI and
are never stored in the pilot workspace, release kit, installer receipt, logs, or public evidence.
Do not copy this file to another user/machine and do not replace it to revive old pending jobs.

The release uninstaller deliberately preserves this state: deleting it automatically could turn an
otherwise inspectable interrupted workspace into unrecoverable ambiguity. If CadPlot is permanently
retired, first archive/review all workspaces and terminal receipts; key-state removal is a separate
authorized local security action, not part of normal bundle/Python uninstall.
