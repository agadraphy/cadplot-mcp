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
- Execution-complete staged manifests and strict .NET manifest/request consistency validation.
- Current-user-only named-pipe access for both .NET 8 and .NET Framework 4.5 adapters.
- Read-only staged-job validation across Python, the local pipe, and the .NET manifest parser.
- Approval-gated batch staging for up to 20 unique DWG/plan-ID pairs per request.
- Optional exact-case canonical media validation carried through plans and staged manifests.
- Parser-based PDF structure, encryption, page-count, and page-dimension output auditing.
- Orientation-independent expected-versus-actual PDF paper-size validation.
- Single-job .NET publish worker with concurrency guard and safe failure-state transitions.
- Label-centric frame deduplication, nested selection, ambiguity rejection, and confidence gate.
- Optional case-insensitive office frame-layer allowlist.
- Reproducible synthetic plan-to-stage-to-PDF-audit demo and integration test.
- Python-to-Windows-pipe and .NET protocol integration tests.
- Opt-in pipe commands for approval-gated publish queueing and bounded job-status reporting.
- Shared main-context AutoCAD executor for named page setups, locked scaled viewports, and
  one-PDF-per-sheet PlotEngine output.
- Byte-preserving execution that discards in-memory layout scaffolding after plotting.
- Plug-in-side staged-DWG SHA-256 revalidation immediately before queueing and execution.
- Compile-only AutoCAD managed-API probe plus a separate licensed-workstation write pilot.
- CI release-archive audit that rejects proprietary CAD/plot assets, secrets, Autodesk assemblies,
  and unexpected binaries from Python distributions or bundle ZIPs.
- Approval-bound publish batching for up to 20 unique manifest/plan/hash triples per call.
- Immutable, manifest-digest-bound terminal publish receipts that survive AutoCAD restarts.
- Cross-checked receipt reading and a `publish_verified` audit gate requiring both valid PDFs and
  successful execution evidence.
- Restartable, cursor-paginated workspace operations reports with safe recovery actions and exact
  requeue approvals for untouched jobs.
- Exact-file AutoCAD bundle verification with module-route, managed-assembly, reparse-point, and
  SHA-256 checks before archive creation or installation.
- Strict office-profile configuration validation for unknown fields, YAML types, finite numeric
  ranges, resource names, and non-overlapping source/workspace directory trees.
- Guarded `-WhatIf`-capable bundle uninstall/upgrade path with exact package identity and
  reparse-point checks.
- Optional approved in-drawing paper-space layout cloning that preserves title-block geometry and
  deterministically retargets exactly one existing floating viewport.
- Strict machine-readable licensed-pilot evidence validation requiring distinct 2016/2025 runs,
  unchanged DWG hashes, manifest-bound receipts, restart proof, and complete visual acceptance.
- Validated optional Codex plugin wrapper for launching the installed local stdio MCP without
  packaging Autodesk or company assets.
- Model-facing MCP server instructions that enforce dry-run-first sequencing, exact plan/manifest
  approvals, immutable sources, and `publish_verified=true` as the completion gate.
- Documented separation between the implemented local workstation worker, ChatGPT web developer
  tunnels, managed company HTTPS gateways, and public plugin submission requirements.

### Notes

- No public alpha has been published yet. Validate plans in dry-run mode and review all
  outputs before use with production drawings.
