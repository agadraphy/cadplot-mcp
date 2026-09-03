# ADR 0010: Public gateway with an outbound workstation worker

- Status: Accepted for implementation
- Date: 2026-09-02

## Context

The local MCP server deliberately accepts workstation paths and talks to AutoCAD through COM and
a current-user named pipe. Its loopback HTTP mode is a tunnel target, not an Internet service.
Putting that endpoint behind a reverse proxy would expose local-path-shaped inputs and outputs and
would collapse the current-user workstation boundary into a public authorization boundary.

A public MCP integration also needs one stable HTTPS endpoint, OAuth resource-server discovery,
per-user authorization, durable routing to an enrolled workstation, revocation, bounded retries,
and reviewable audit evidence. None of those controls grant an Autodesk license: AutoCAD must
remain installed, licensed, and user-visible on each user's Windows workstation.

## Decision

CadPlot will use two separately deployable Python applications plus the existing AutoCAD bundle:

1. `cadplot-gateway` runs on a public HTTPS origin. It contains no AutoCAD, COM, Autodesk assembly,
   local configuration, or filesystem-path handling. It validates an OAuth bearer token on every
   MCP request, derives tenant and user identity from the verified token, applies tool scopes, and
   routes closed command types through a durable operation queue.
2. `cadplot-worker` runs in the same interactive Windows user session as the licensed AutoCAD
   process. It opens only outbound HTTPS connections to the gateway. It resolves opaque project,
   drawing, and job identifiers to locally configured paths, re-applies `PathPolicy` on every use,
   invokes the existing deterministic CadPlot core, and sanitizes every result before egress.
3. `CadPlotMcp.bundle` remains local. The gateway can never select a COM ProgID, pipe name, allowed
   root, configuration path, workspace path, or publish-enable flag.

The gateway and worker exchange a versioned, closed protocol. There is no generic remote
`tool_name`/`args`, shell, AutoLISP, COM, named-pipe, URL-fetch, or arbitrary filesystem command.
Public schemas use only tenant-bound opaque identifiers. Raw DWG, DWT, CTB/STB, PC3/PMP,
configuration, and local path data never cross the gateway boundary.

The first public-capable increment is read-only: workstation/project listing, environment status,
drawing discovery, inspection, planning, and asynchronous operation status. Public publish remains
disabled until the write protocol, server-side approvals, crash reconciliation, and licensed
AutoCAD acceptance gates are complete.

## Identity and authorization

- The MCP endpoint is an OAuth 2.1 resource server. It advertises protected-resource metadata and
  accepts tokens only for the configured issuer and resource audience.
- Tenant and user identifiers are derived from verified claims. Client-provided tenant headers or
  tool parameters are ignored; every lookup is bound to the derived principal.
- Read, stage, publish, cancel, and artifact access are distinct scopes. Possession of a plan hash,
  manifest hash, operation ID, or opaque local reference grants no authority by itself.
- A worker is enrolled to one tenant and user through a short-lived, one-use browser-assisted code.
  Its long-lived private credential belongs in a Windows user-bound credential store, is rotatable,
  and is independently revocable. Enrollment identifies a device; it does not attest licensing.

## Operation protocol

Each task binds `protocol_version`, task and operation IDs, tenant, device, one closed command,
an idempotency key, issue/expiry times, and a short lease. The gateway persists the operation and
task atomically before returning. A worker may lease only its own tasks and must validate identity,
schema, size, expiry, and command allowlist before resolving any local reference.

Read operations may be delivered at least once and return a cached result for the same idempotency
key. Mutations must not be blindly replayed. Their eventual state machine is:

`CREATED -> LEASED -> RUNNING -> SUCCEEDED | FAILED | EXPIRED | ATTENTION_REQUIRED`

The public MCP call waits only for a short bounded interval. If the workstation does not finish in
that interval, it returns an opaque operation ID for `get_operation`; it does not hold a public HTTP
request open for AutoCAD-scale work.

## Write extension requirements

Stage, queue, and cancel are intentionally outside the first increment. Before enabling them:

- The gateway must issue single-use approval records bound to the exact subject, tenant, device,
  action, drawing/job reference, `plan_id`, `manifest_sha256` where applicable, policy version, and
  expiry. Consumption is atomic and authorization is rechecked at dispatch time.
- The worker must keep a durable remote-task journal. A crash after a mutation begins becomes
  `ATTENTION_REQUIRED` unless an exact local receipt proves the result; it is never auto-replayed.
- Queue status and reconciliation must bind the exact manifest hash, not only the plan ID.
- Completion requires both `source_unchanged=true` and `publish_verified=true` from the local audit.
- Cancellation remains exact and Pending-only. A running AutoCAD plot is not force-terminated.

## Data and logging

Gateway persistence contains opaque identities, policy decisions, bounded sanitized results,
operation transitions, approval digests, and audit correlations. It must not contain OAuth/device
tokens, raw exception text, local paths, DWG contents, office resources, or unapproved PDFs.
Generated PDFs remain local by default. Any future artifact transfer is separate opt-in behavior
using a server-chosen object key, short-lived upload/download authorization, encryption, retention,
and a receipt-bound hash check.

## Consequences

- Users get one public MCP URL while their licensed AutoCAD and proprietary files remain local.
- The gateway and local product can evolve and be reviewed independently; the cloud image cannot
  accidentally import or package `pywin32` or Autodesk components.
- A workstation must be online for new operations. Offline state is explicit and never falls back
  to another user, tenant, or device.
- Public availability still depends on production hosting, an owned verified domain, an OAuth
  provider, worker enrollment/revocation, privacy and support pages, licensed AutoCAD acceptance,
  security review, and OpenAI review. This ADR does not claim those external gates are complete.
