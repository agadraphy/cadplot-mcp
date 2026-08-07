# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Initial safety-first AutoCAD drawing inspection and plot-planning MCP server.
- Copy-only and dry-run-oriented workflow safeguards.
- AutoCAD 2016 (`net45`) and AutoCAD 2025–2026 (`net8.0-windows`) adapter scaffolds.
- Version-routed Autodesk `.bundle` manifest and guarded bundle/install scripts.
- Whitelisted, size-limited read-only named-pipe status protocol.
- Hash-verified, read-only publish-plan preview across the Python and .NET boundary.
- Content-bound plan fingerprints and approval-gated, copy-only job staging.
- Per-job JSON manifests with collision-free expected PDF paths.
- Read-only PDF output auditing with path-containment, header, size, and SHA-256 checks.
- Bounded .NET publish-job queue with trusted-workspace validation and duplicate prevention.
- Deterministic plot-window, rotation, and allowed-scale derivation from frame geometry.
- ISO A0-A5 paper-label recognition in addition to dimension labels.
- Restartable, deterministic batch-plan pages with per-DWG failure isolation.
- Named page-setup inspection and configured plotter/plot-style consistency checks.
- Deterministic target-layout naming with existing-layout collision blockers.
- Python-to-Windows-pipe and .NET protocol integration tests.

### Notes

- No public alpha has been published yet. Validate plans in dry-run mode and review all
  outputs before use with production drawings.
