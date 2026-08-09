# ADR 0004: Durable approved queue intent without automatic interrupted replay

**Status:** Accepted and implemented; authentication amended by ADR 0005; licensed AutoCAD acceptance pending
**Date:** 2026-08-09  
**Deciders:** CadPlot maintainer; company CAD/IT owners approve live policy

## Context

ADR 0002 kept pending/running state only in the AutoCAD process. A restart during a 300-drawing run
therefore lost accepted pending work and live status. Blindly reconstructing every unfinished job
would create a worse risk: a job that produced some output immediately before a crash could plot a
second time without a new human decision.

Constraints are AutoCAD 2016/.NET Framework 4.5 compatibility, no external database, immutable
plan/manifest approval, no source-DWG writes, and bounded machine-readable failure codes.

## Decision

Each accepted job receives an immutable job-local `.cadplot-queue-request.json` only after the exact
request passes the existing workspace, manifest, staged-DWG, output, and hash validators. Before
execution, the worker atomically creates `.cadplot-queue-started.json`.

ADR 0005 additionally authenticates both records with a Windows user-bound key outside the workspace;
the job-local file alone is therefore no longer treated as proof that CadPlot accepted the request.

At plug-in startup the queue scans only bounded, direct, valid job-ID directories:

- request without started marker or receipt: revalidate and restore `Pending` with exact identity;
- request plus started marker and valid receipt: restore terminal `Succeeded`/`Failed` status;
- request plus started marker but no receipt: expose `Failed/job_interrupted`, never auto-replay;
- corrupt, redirected, duplicate, over-capacity, or identity-mismatched evidence: disable publish
  initialization and expose only `publish_queue_initialization_failed`.

Status telemetry includes recovered and interrupted startup counts. Five exact production-core tests
are parsed from TRX and bound into local readiness, demo-kit, and release-kit evidence.

## Options Considered

### Option A: Keep process-only state

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Restart resilience | Low |
| Duplicate-output safety | Medium |

**Pros:** No disk state.  
**Cons:** Accepted work disappears on restart; poor fit for large batches.

### Option B: Automatically replay every unfinished job

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Restart resilience | High |
| Duplicate-output safety | Low |

**Pros:** Maximum unattended continuation.  
**Cons:** Ambiguous crash point can cause unintended repeated plotting.

### Option C: Immutable intent plus started marker

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Restart resilience | High for never-started work |
| Duplicate-output safety | High |

**Pros:** Preserves explicit approval and safely resumes only unambiguous pending work.  
**Cons:** Interrupted work requires review and a new staged job.

## Trade-off Analysis

Option C favors auditability and duplicate-output prevention over fully unattended crash replay.
This matches CadPlot's fail-closed safety contract and keeps all mutable state inside the already
trusted workspace.

## Consequences

- AutoCAD restart no longer erases accepted but never-started pending work.
- Terminal live status can be reconstructed from the immutable receipt.
- Interrupted work is visible and bounded but deliberately requires operator action.
- Queue marker corruption can disable publishing until the workspace is reviewed.
- Compile/tests prove production-core logic only; live 2016/2025 behavior remains a separate gate.

## Action Items

1. [x] Persist and recover exact queue intent.
2. [x] Prevent automatic interrupted replay and restore terminal receipt status.
3. [x] Bind fifteen exact recovery/authentication/cancellation/tamper tests into release evidence.
4. [ ] Accept restart behavior separately on licensed AutoCAD 2016 and 2025.
