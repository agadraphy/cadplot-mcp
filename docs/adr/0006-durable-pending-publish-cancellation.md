# ADR-0006: Durable cancellation for pending publish jobs

**Status:** Accepted
**Date:** 2026-08-09
**Deciders:** CadPlot MCP maintainers and licensed-pilot operator

## Context

A large approved batch can reveal a wrong office profile, title block, or output policy after some
jobs have been queued. The queue previously exposed no bounded stop path. Killing AutoCAD can leave a
running job ambiguous, while deleting a pending marker can allow unsafe replay or destroy audit state.
Cancellation must preserve the original approval record, survive plug-in restart, remain exact to one
manifest identity, and never pretend that AutoCAD can safely abort an active PlotEngine operation.

## Decision

Add an idempotent `cancel_publish_job` control for an exact `plan_id` and `manifest_sha256`. It may
transition only `Pending` to `Cancelled`. Before changing memory state, the plug-in atomically creates
an HMAC-SHA256 authenticated `.cadplot-queue-cancelled.json` marker beside the signed pending intent.
The authentication key remains DPAPI-protected outside the workspace under ADR-0005.

Restart recovery verifies the pending and cancellation records, restores `Cancelled`, and never
replays it. A missing, altered, redirected, unsigned, identity-mismatched, or conflicting cancellation
record disables queue initialization. Retrying the same exact cancellation succeeds idempotently.
Running or terminal jobs return `job_not_pending`; CadPlot never interrupts an active plot.

The read-only operations report treats a structurally valid cancellation marker as a fail-safe
`cancelled_hold` and omits requeue approval. Only the live plug-in can verify its HMAC and report the
authoritative `Cancelled` state.

## Options Considered

### Option A: Signed pending-only cancellation tombstone

| Dimension | Assessment |
| --- | --- |
| Complexity | Medium |
| Data safety | High |
| Restart behavior | Deterministic and fail-closed |
| AutoCAD 2016/2025 portability | Core-only; no version-specific abort API |

**Pros:** exact, durable, retry-safe, auditable, and testable without launching AutoCAD.

**Cons:** cannot stop the job after it has entered `Running`; cancelled staging remains for review.

### Option B: Force-abort AutoCAD or PlotEngine

| Dimension | Assessment |
| --- | --- |
| Complexity | High |
| Data safety | Low |
| Restart behavior | Ambiguous partial outputs |
| AutoCAD 2016/2025 portability | Version-sensitive |

**Pros:** may stop work sooner.

**Cons:** risks partial PDF/layout state, application instability, and inconsistent version behavior.

### Option C: Delete or rename pending records

| Dimension | Assessment |
| --- | --- |
| Complexity | Low |
| Data safety | Low |
| Restart behavior | Loses authorization/audit history |
| AutoCAD 2016/2025 portability | Portable but unsafe |

**Pros:** simple implementation.

**Cons:** destructive, difficult to retry safely, and absence cannot prove an authorized cancellation.

## Trade-off Analysis

CadPlot favors preservation and deterministic recovery over immediate interruption. The bounded queue
limits how much already-approved work can be pending, while signed cancellation provides a safe way to
stop that backlog. Active PlotEngine cancellation remains an explicit unsupported boundary.

## Consequences

- Operators can durably stop exact pending jobs without closing AutoCAD.
- Cancelled jobs cannot be requeued in place; a later retry requires a newly staged job and approval.
- The MCP tool is marked destructive because it creates an irreversible queue tombstone, but it is
  idempotent for the same exact identity.
- Queue telemetry reports how many cancelled jobs were restored at startup.
- Licensed pilots must still verify cancellation timing and UI/operator behavior separately on 2016
  and 2025; core tests are not live AutoCAD proof.

## Action Items

1. [x] Add authenticated cancellation records and pending-only state transitions.
2. [x] Add MCP/pipe control, telemetry, operations-report hold, and negative tests.
3. [ ] Exercise cancellation during separate licensed AutoCAD 2016 and 2025 pilot runs.
