# Verified Release Kit Installation

This procedure installs a release kit that was built from one clean Git commit with real AutoCAD
2016 (`R20.1`) and AutoCAD 2025 (`R25.0`) managed API references. It does not prove a live publish.
Keep AutoCAD closed until the bundle copy and environment configuration are complete.

## 1. Verify before installing

From the source checkout matching the kit commit:

```powershell
.\scripts\verify-release-kit.ps1 -ReleaseRoot C:\CadPlotTransfer\cadplot-release-kit-0.1.0-abcdef0
```

The verifier checks the exact directory set, every SHA-256 digest, the embedded bundle build/API
identity, and every ZIP entry without extracting the archive. Do not install a kit that fails.

## 2. Install the AutoCAD bundle

Preview the exact destination first, then repeat without `-WhatIf`:

```powershell
$kit = "C:\CadPlotTransfer\cadplot-release-kit-0.1.0-abcdef0\CadPlotMcp.release"
.\scripts\install-bundle.ps1 -SourceBundle "$kit\autocad\CadPlotMcp.bundle" -WhatIf
.\scripts\install-bundle.ps1 -SourceBundle "$kit\autocad\CadPlotMcp.bundle"
```

The installer never overwrites an existing `CadPlotMcp.bundle`. Use the verified uninstaller with
AutoCAD closed before an upgrade.

## 3. Install the Python MCP command

Install the exact wheel into an isolated `uv` tool environment:

```powershell
$wheel = @(Get-ChildItem "$kit\python\cadplot_mcp-*-py3-none-any.whl" -File -ErrorAction Stop)
if ($wheel.Count -ne 1) { throw "Expected exactly one CadPlot MCP wheel." }
$wheelPath = ($wheel | Select-Object -First 1).FullName
uv tool install $wheelPath
cadplot-doctor --help
```

The included `pyproject.toml`, `uv.lock`, and commit-bound source ZIP are retained for dependency
review and reproducible maintenance. The wheel contains no Autodesk or company assets.

## 4. Add the authorized office profile

Copy `config/config.inventory.example.yaml` outside the release kit and replace its deliberately
non-matching placeholders only with values inventoried on the licensed company workstation. Do not
copy company DWG, PC3, PMP, CTB/STB, DWT, credentials, or local `config.yaml` into the repository or
release kit.

Set `CADPLOT_CONFIG` to that external file. Keep `CADPLOT_ENABLE_PUBLISH` unset during inspection and
dry-run. Follow `docs/pazartesi-demo-tr.md` for the licensed one-sheet acceptance flow.

## Evidence boundary

Successful kit verification means the two installers and their source are cryptographically bound
to one commit. It does not mean AutoCAD was launched or PDF publishing was proven. Record AutoCAD
2016 and 2025 live evidence separately before any production-ready claim.
