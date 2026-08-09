# ADR 0005: Authenticate durable publish intent outside the job workspace

**Status:** Accepted and implemented; licensed AutoCAD acceptance pending
**Date:** 2026-08-09
**Deciders:** CadPlot maintainer; company CAD/IT owners approve live key policy

## Context

ADR 0004 made accepted queue intent durable by storing request and started markers beside each staged
job. Their fields and referenced files were revalidated, but a structurally valid marker was not
cryptographically distinguishable from one written by another process with workspace write access.
Because an authentic pending marker may execute automatically after AutoCAD restarts, workspace
contents cannot be their own proof of prior approval.

Constraints are Windows-only AutoCAD 2016/.NET Framework 4.5 and AutoCAD 2025/.NET 8 compatibility,
no external secret service, no Autodesk or company asset redistribution, no secret in the public
release evidence, and fail-closed behavior when identity cannot be established.

## Decision

CadPlot creates a random 256-bit queue-authentication key outside the trusted job workspace under the
current user's local application state. The stored key is encrypted with Windows DPAPI `CurrentUser`
scope and written atomically without overwrite. Queue request and started records carry versioned
HMAC-SHA256 tags over a deterministic length-prefixed binary encoding of every security-relevant
field.

Recovery and start execution verify the tag before trusting or interpreting record paths, hashes,
counts, timestamps, or state. Unsigned, altered, foreign-key, redirected, malformed, or workspace-local
key evidence disables recovery or blocks start. Status reports the exact scheme
`windows-dpapi-current-user+hmac-sha256-v1`; batch telemetry rejects a publish-enabled plug-in that
does not report that scheme.

The key value, protected blob, and local path never enter readiness, demo, release, pilot, or public
evidence. Only the scheme and synthetic pass/fail claims are portable.

## Options Considered

### Option A: Continue trusting validated workspace markers

| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Forgery resistance | Low |
| AutoCAD compatibility | High |

**Pros:** No key lifecycle.
**Cons:** A copied or locally fabricated valid marker can imitate prior approval.

### Option B: DPAPI-protected local HMAC key

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Forgery resistance | High across the workspace boundary |
| AutoCAD compatibility | High; native Windows API works on both targets |

**Pros:** No external service or redistributable dependency; deterministic offline verification.
**Cons:** Moving a workspace to another Windows user/machine cannot preserve pending authorization.

### Option C: Public-key signatures from the Python/MCP process

| Dimension | Assessment |
|---|---|
| Complexity | High |
| Forgery resistance | High |
| Operational coupling | High |

**Pros:** AutoCAD would need only a public verification key.
**Cons:** Introduces private-key provisioning into ChatGPT/MCP, cross-process signing, rotation, and
installer policy before a job can be queued.

### Option D: Windows Credential Manager secret

| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Forgery resistance | High across the workspace boundary |
| Enterprise availability | Variable |

**Pros:** No explicit key file.
**Cons:** Credential Manager policy/service availability varies; harder to diagnose and back up safely.

## Trade-off Analysis

Option B protects the actual trust-boundary crossing without turning the local AutoCAD plug-in into a
networked key service. It intentionally binds pending approval to one Windows user profile. Losing or
changing that profile causes a safe authentication failure instead of silently authorizing a replay.
It does not claim protection against malicious code already running as the same Windows user.

## Consequences

- Workspace contents alone can no longer authorize restart execution.
- Altering any signed request/started field is detected before plotting.
- Copying a job workspace to another user or machine leaves files inspectable but pending work is not
  automatically replayed.
- A missing/corrupt DPAPI key blocks publish initialization; operators must preserve evidence and
  create a fresh staged approval rather than replacing a key to revive old intent.
- The threat boundary remains the current Windows user account; account compromise is outside this
  local-control design.
- Production-core tests prove the cryptographic workflow on Windows; licensed 2016/2025 behavior is
  still a separate live gate.

## Action Items

1. [x] Store a no-overwrite DPAPI-protected 256-bit key outside the trusted workspace.
2. [x] Authenticate request and started records with deterministic HMAC-SHA256.
3. [x] Reject unsigned, altered, foreign-key, corrupt-key, and workspace-local-key recovery.
4. [x] Report and require the authentication scheme in live queue telemetry.
5. [x] Bind exact authentication tests into readiness/demo/release evidence.
6. [x] Execute DPAPI key creation from the built `net45` core under Windows .NET Framework.
7. [ ] Accept authenticated restart behavior separately on licensed AutoCAD 2016 and 2025.
