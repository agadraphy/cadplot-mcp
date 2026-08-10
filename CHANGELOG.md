# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- Normal output auditing now re-fingerprints the authorized source DWG at completion and requires
  its SHA-256, byte length, and modification timestamp to match the staged approval before
  `publish_verified=true`. Changed sources are reported as `source_changed`, receive no restart
  requeue approval, and are rejected by staged validation and queueing before any plug-in pipe
  call, preventing a valid receipt for an older snapshot from being mistaken for the latest
  revision.
- Pilot evidence schema v7 now makes the bounded post-restart batch gate non-optional. The new
  `cadplot-collect-recovery` command accepts only an exact isolated 2–20 job workspace, replays each
  manifest, successful receipt, output binding, and source/staged hash, and binds separate 2016 and
  2025 recovery records to the verified running adapters before final acceptance can claim live
  readiness. Sanitized release acceptance schema v2 retains the accepted recovery releases and job
  counts without exposing company paths, job identities, or file hashes.
- Physical page orientation is now exact across planning, AutoCAD page-setup validation, and PDF
  audit. Ordered width/height and effective PDF `/Rotate` are checked, the 300-drawing rehearsal
  proves a portrait-for-landscape mismatch is rejected, and delivery verifiers bind that evidence.
  Licensed visual-reference collection applies the same effective `/Rotate` rule and rejects
  non-quarter-turn reference pages.
- Standalone bundle installation is now bound to the full verified release root: the exact sibling
  ZIP, `bundle-build.json`, commit identity, and matching-SDK evidence are rechecked before any
  copy. Protocol-only fixtures fail normal installation and require explicit test-only consent.
- Matching R20.1/R25.0 bundles now compile in fresh release-specific temporary trees with
  incremental/shared compilation disabled, so stale ignored repository outputs can never be
  packaged as production adapters. The cross-version Autodesk runtime also has an explicit
  nullable context so enabling the 2025 SDK path compiles under the repository's warnings-as-errors
  policy.
- AutoCAD package, COM selection, runtime guard, compile probe, and offline verifier now share the
  exact support boundary `R20.1` plus `R25.0`. The former `R25.1` route was removed because Autodesk
  identifies it as AutoCAD 2026, which has no licensed acceptance in this project.
- Symmetric `CADPLOT_PIPE_NAME` selection in the Python client and both AutoCAD adapters, plus
  bounded version-specific `CADPLOT_AUTOCAD_PROGID` inspection. Safe pipe names are validated by
  the shared .NET protocol; COM selection accepts only approved AutoCAD identities and verifies the
  returned version, enabling separately addressed licensed 2016 and 2025 processes on one
  workstation while invalid or mismatched identities fail closed. The host reserves its single
  pipe instance synchronously and reuses it across requests, so a duplicate bridge name fails during
  initialization and cannot race into the gaps between normal MCP calls.

- Schema-v2 terminal publish receipts now bind the exact ordered PDF set by sheet index, filename,
  byte length, and SHA-256. The .NET restart path and independent Python audit recompute the same
  canonical digest; missing, replaced, redirected, or changed PDFs fail closed even when the
  replacement remains a structurally valid one-page PDF. Licensed pilot evidence advances to
  schema v6 and records the independently verified binding. Schema v7 supersedes it by additionally
  requiring per-release batch recovery evidence.

- One-command, no-overwrite portable demo delivery packaging with an exact kit directory, matching
  ZIP, and outer manifest. Its embedded verifier checks safe ZIP paths, exact entry membership,
  per-entry hashes, inner manifest/SBOM identity, and false live-evidence boundaries without
  extraction; smoke tests reject outer-identity, archive-byte, and traversal tampering.

- Deterministic CycloneDX 1.7 JSON SBOM generation and strict offline validation, binding the clean
  commit, package version, `uv.lock`, exported production requirements, licensed Python inventory,
  wheel/source artifacts, AutoCAD bundle, and four shipped first-party DLLs. Demo/release manifests,
  self-verifiers, archives, and semantic tamper smokes now require the path-redacted SBOM while
  retaining false live-publish claims; Authenticode remains an external certificate gate.

- Canonical, tool-surface-hash-bound 13-case ChatGPT tool-selection evaluation covering direct,
  indirect, follow-up, approval, adversarial, edge, pending-cancellation, and recovery behavior.
  The installed no-overwrite preparer/validator retains only sanitized tool-name attestations,
  fails closed on forbidden writes or reordered calls, and never upgrades its evidence into live
  publish or licensed AutoCAD acceptance. Tunnel preflight now writes optional BOM-free UTF-8
  evidence with `--output`.
- Exact, idempotent pending-job cancellation with an authenticated durable tombstone, restart
  non-replay, `Cancelled` live state, cancellation telemetry, fail-safe operations-report hold, and
  explicit refusal to interrupt a running PlotEngine job. Fifteen exact queue scenarios now bind
  recovery, authentication, cancellation, and tamper evidence.
- Windows-user-bound durable queue authentication: a no-overwrite DPAPI-protected 256-bit key
  outside the workspace signs exact request and started records with deterministic HMAC-SHA256.
  Unsigned, altered, foreign-key, or workspace-local key evidence fails closed; status and batch
  telemetry expose/require the exact scheme. Fifteen exact tests plus a built-net45 Windows Framework
  DPAPI runtime probe are bound into release evidence.
- Secret-free `cadplot-tunnel-preflight` administrator handoff for OpenAI Secure MCP Tunnel,
  including local config/command validation, STDIO-first and fixed-loopback command templates,
  redacted credential presence checks, and explicit external workspace/network/live-pilot gates.
- Optional real local MCP `initialize/list_tools` probing in tunnel preflight, with exact 20-tool
  identity/instruction/annotation/schema validation and a contract digest; isolated-wheel, demo-kit,
  and release-kit evidence now bind the successful redacted probe without calling AutoCAD.
- Root-local virtual environment variants are explicitly excluded from source distributions so
  Python matrix environments cannot leak into release archives.
- Durable job-local queue intent for explicitly approved plan/manifest identities: never-started
  pending work is revalidated after restart, receipt-backed terminal status is restored, and an
  interrupted running job is held as `job_interrupted` instead of being replayed. Readiness,
  demo-kit, and release-kit evidence bind fifteen exact production-core recovery/authentication/tamper
  scenarios.
- Flat, hash-verified Turkish Monday runbook and Secure MCP Tunnel handoff files in the portable
  local demo kit, so operators do not need to extract the commit-bound source archive first.
- Live publish-queue capacity telemetry on status/queue/job responses plus batch response schema v2,
  which treats `queue_full` as retryable backpressure, defers untouched approvals, and stops further
  pipe calls until slots reopen for safer one-prompt large-run orchestration; the 300-drawing
  rehearsal and release evidence chain now enforce a saturated seven-slot exact-retry simulation.
- A bounded read-only `get_publish_batch_status` MCP tool summarizes up to 20 exact plan IDs and
  returns a separately sampled, internally consistent final queue telemetry view for efficient
  large-run polling; the 300-drawing release rehearsal requires 15 identity-preserving status
  batches.
- Read-only bounded block-definition frame labels, including nested definitions, with no `Explode`,
  strict depth/entity/text caps, cycle/Xref/layout rejection, lower confidence, and fail-closed
  handling whenever a detected paper label came from incomplete traversal.
- Explicit hash-pinned external DWG/DWT layout templates with separate read-only roots, isolated
  copy-only job staging, manifest-bound asset identities, dual Python/plug-in hash replay, verified
  single-viewport planning, and cross-database import that never replaces source definitions.
- Pilot-evidence schema v4 records path-redacted external-template identity and post-pilot hashes,
  binds the exact published and authorized-reference PDFs by SHA-256/size/page geometry, revalidates
  orientation/size agreement, and replaces blanket visual acceptance with seven explicit checks.
- One-command verified release orchestration for local rehearsal, dependency audit, exact R20.1/R25.0
  adapter builds, bundle/release-kit assembly, and independent verification without launching AutoCAD
  or accepting/redistributing Autodesk SDK files.
- Job-level PDF output transactions that plot every sheet to owned same-directory partial files,
  discard the staged DWG, then no-overwrite-promote complete outputs; ordinary mid-job failures now
  clean partials without exposing earlier sheets as final PDFs, and mid-promotion failures attempt
  reverse-order no-overwrite rollback before reporting an explicit partial-commit condition.
- Fail-closed post-pilot release acceptance finalizer/validator that binds the exact release kit,
  wheel, matching-SDK bundle, and separate licensed 2016/2025 evidence into a sanitized no-overwrite
  report while keeping public readiness behind explicit company and maintainer approvals.
- Full-commit-pinned, read-only GitHub Actions with non-persistent checkout credentials, workflow
  supply-chain auditing, explicit action-repository allowlisting, Python 3.11/3.12/3.13 compatibility,
  and weekly Dependabot coverage for `uv`, NuGet, and Actions dependencies.
- Receipt-bound resumable release uninstaller with AutoCAD process guard, full `-WhatIf` child
  preflight, bundle-before-Python removal, idempotent partial-run recovery, and deliberate pilot/
  install-receipt preservation.
- Independent installed-release verifier that replays the receipt digest, transferred release kit,
  exact bundle hashes, Python install inventory, and pilot paths without launching AutoCAD; expected
  post-install inventory-config changes are reported separately from immutable evidence failures.
- No-overwrite local installation receipts binding release/Python manifests, exact bundle hashes,
  component paths, and false live/publish claims; bundle mutations now require `acad.exe` to be closed.
- Resumable single-command release-kit installer that revalidates the whole transfer, rejects
  overlapping paths, reuses only exact verified components, and exposes the AutoCAD bundle last.
- Long-path-safe exact quarantine deletion for verified Python and AutoCAD bundle removals on
  Windows PowerShell 5.1, covered by the orchestrated installation smoke's deep virtual environment.
- Double-verified AutoCAD bundle removal with package/hash identity replay, atomic non-`.bundle`
  quarantine rename, fail-closed retention, and protocol-fixture quarantine cleanup proof.
- Verified Python uninstaller with `-WhatIf`, repeated manifest/environment validation, redirected-
  path rejection, atomic non-loadable quarantine rename, and exact-target removal smoke coverage.
- Release-kit Python installer/verifier with embedded kit revalidation, frozen hash-required
  dependency installation, wheel `--no-deps`, isolated staging, no-overwrite atomic naming,
  installed-distribution evidence, and protocol-fixture tamper/WhatIf/overwrite smoke coverage.
- Loopback-only Streamable HTTP `/mcp` entry point for an authorized Secure MCP Tunnel, with fixed
  `127.0.0.1` binding, strict Host/Origin checks, a 1 MiB request cap, real protocol smoke coverage,
  and explicit separation from unimplemented managed OAuth/HTTPS deployment.
- Bounded per-DWG inspection deadlines after helper startup through a killable subprocess with
  closed stdin/JSON protocol, bounded output, exact path replay, and batch-level timeout isolation
  without terminating AutoCAD.
- Read-only detection of orthogonal attribute-backed block frames with fail-closed rejection of
  conflicting labels, mismatched bounds, non-orthogonal rotation, and equal-size competition.
- Dry-run and onboarding enforcement that approved named page setups use `PlotType=Layout`, matching
  the existing plug-in-side runtime guard.
- Independent manifest geometry replay for paper orientation, derived/selected scale, tolerance,
  and frame aspect, plus read-only and plug-in-side enforcement that named paper-space page setups
  plot at exactly 1:1 rather than multiplying viewport scale with scale-to-fit/custom scaling.
- Fail-closed rejection of legacy staged manifests without the complete geometry replay contract;
  affected jobs must be planned and staged again after upgrade.
- Lock-bound, network-backed Python and .NET dependency vulnerability evidence, including a complete
  transitive Python license inventory. CI and release kits fail closed on known vulnerabilities,
  missing license declarations, incomplete project coverage, or a changed `uv.lock`.
- Release-aware compile probing that targets AutoCAD 2016 `R20.1` with `net45`, exact AutoCAD 2025
  `R25.0` with `net8.0-windows`, supported intervening releases with `net48`, and rejects unknown
  or unsupported later API series without launching AutoCAD.
- Runtime `ACADVER` normalization and adapter/release gating that keeps publishing disabled when a
  2016 or 2025 plug-in is loaded into an unsupported AutoCAD runtime.
- Self-verifying local demo kits with exact-tree/hash and tamper checks, plus redacted compile-only
  API evidence that does not expose the build workstation's local API directory.
- Self-verifying transfer kits that carry their exact verifier, portable pilot-workspace setup,
  licensed-run evidence tools, and installed `cadplot-collect-pilot`, `cadplot-collect-recovery`,
  `cadplot-assemble-pilot`, and `cadplot-validate-pilot` commands without requiring a separate
  source checkout.
- Live plug-in build-commit and adapter-binary SHA-256 identity, plus schema-v2 pilot evidence that
  binds both licensed runs to the exact matching-SDK bundle manifest and inner adapter hashes.
- A 300-drawing synthetic batch rehearsal covering immutable planning pages, bounded staging and
  queue-approval protocol, restart-report pagination, source preservation, and 300 structural PDF
  audits while deliberately retaining zero live execution/publish verification.
- Commit-bound combined release-kit builder/verifier for the matching-SDK AutoCAD bundle,
  readiness-bound Python wheel, locked project metadata, source archive, safe install scripts, and
  pilot documentation. Both directory and ZIP contents are exact/hash verified while all live and
  public-readiness claims remain false.
- Initial safety-first AutoCAD drawing inspection and plot-planning MCP server.
- Copy-only and dry-run-oriented workflow safeguards.
- AutoCAD 2016 (`net45`) and AutoCAD 2025 (`net8.0-windows`) adapter scaffolds.
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
- Read-only licensed-run collector and no-overwrite two-release evidence assembler that eliminate
  manual hash copying while preserving explicit license, asset, restart, and visual attestations.
- Fail-fast Windows preflight command for locked Python checks, synthetic workflow, release audit,
  .NET build/tests, and an optional compile-only installed AutoCAD API probe without launching CAD.
- Deliberately non-matching inventory configuration and read-only onboarding flow for discovering
  exact authorized office page setup, plotter, style, media, frame, and template-layout names.
- Read-only `inventory_office_resources` MCP tool that deduplicates those observed names while
  marking every paper-space template as an unapproved candidate requiring viewport validation.
- Read-only `cadplot-doctor` CLI with config-only, inspection, and full local plug-in diagnostic
  modes plus machine-readable readiness and failure output.
- `-WhatIf`-capable, no-overwrite local pilot initializer that creates only an inventory config and
  empty separated input/workspace directories without copying company assets or enabling publish.
- Real subprocess MCP `stdio` smoke test covering initialization, the exact 18-tool surface, server
  safety instructions, and read/write/destructive/open-world annotations.
- Bounded, cancelable, read-only GitHub Actions permissions with separately visible MCP protocol
  and synthetic workflow smoke steps.
- One-MiB configuration size limit and normalized fail-closed errors for malformed YAML or invalid
  UTF-8 before any AutoCAD connection is attempted.
- Strict MCP input schemas for exact plan/manifest identifiers, closed batch approval objects,
  1-20 approval counts, bounded pagination/file limits, non-empty paths, and bounded timeouts.
- Concise human-facing titles for all 18 MCP tools, preserving visible dry-run and explicit approval
  distinctions in ChatGPT/Codex tool interfaces.
- Model-facing parameter descriptions that identify local path policy, pagination cursors, bounded
  timeouts, and exact plan/hash values that must be copied from preceding trusted steps.
- Closed top-level structured-output schemas for all 18 MCP tools, with exact plan/receipt digest
  patterns and real STDIO `call_tool` checks for profile matching and bounded DWG discovery.
- Isolated-venv wheel installation smoke with frozen, hash-checked lock dependencies that proves
  the built distribution imports outside the source tree and serves the real MCP STDIO contract.
- Metadata-bound batch inventory IDs that make later planning pages fail closed when a large DWG
  folder changes between offsets, preventing silent skips or duplicates in 300-drawing runs.
- Early source-tree audit for tracked or stageable proprietary CAD/plot assets, archives, local
  configuration, Autodesk assemblies, oversized files, and high-confidence credential patterns.
- Transactional AutoCAD bundle installation through a non-loadable staging directory, exact
  source/copy hash comparison, atomic final rename, redirected-file rejection, and a protocol-only
  install/verify/uninstall smoke that never launches AutoCAD.
- Managed API identity checking that binds real adapter builds to consistent `R20.1` (AutoCAD
  2016) and `R25.0` (AutoCAD 2025) Autodesk assembly series instead of trusting DLL filenames.
- Clean-commit, no-overwrite bundle release directories with source/archive audits, exact build
  manifests, and independent ZIP-entry/hash verification that remains explicitly non-live evidence.

### Fixed

- Windows Streamable HTTP smoke cleanup now keeps the server cwd outside disposable evidence,
  waits for process exit, and retries transient directory-lock removal with bounded regression proof.

### Notes

- No public alpha has been published yet. Validate plans in dry-run mode and review all
  outputs before use with production drawings.
