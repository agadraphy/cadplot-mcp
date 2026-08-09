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

## 2. Install the AutoCAD bundle

Preview the exact destination first, then repeat without `-WhatIf`:

```powershell
$kit = "C:\CadPlotTransfer\cadplot-release-kit-0.1.0-abcdef0\CadPlotMcp.release"
.\scripts\install-bundle.ps1 -SourceBundle "$kit\autocad\CadPlotMcp.bundle" -WhatIf
.\scripts\install-bundle.ps1 -SourceBundle "$kit\autocad\CadPlotMcp.bundle"
```

The installer never overwrites an existing `CadPlotMcp.bundle`. Use the verified uninstaller with
AutoCAD closed before an upgrade.

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
& "$commandRoot\cadplot-assemble-pilot.cmd" --help
& "$commandRoot\cadplot-validate-pilot.cmd" --help
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
licensed runs, use the installed `cadplot-collect-pilot`, `cadplot-assemble-pilot`, and
`cadplot-validate-pilot` commands documented in `docs/pilot-evidence.md`; no repository Python
environment is required.

## Evidence boundary

Successful kit verification means the two installers and their source are cryptographically bound
to one commit. It does not mean AutoCAD was launched or PDF publishing was proven. Record AutoCAD
2016 and 2025 live evidence separately before any production-ready claim.
