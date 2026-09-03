# Public deployment

CadPlot's public topology gives MCP clients one HTTPS endpoint while keeping AutoCAD and CAD files
on each user's licensed Windows workstation.

```text
MCP client
  | OAuth bearer token
  v
cadplot-gateway (public HTTPS /mcp)
  | tenant-scoped durable task, no local paths
  v
cadplot-worker (outbound HTTPS only)
  | allowed local reference + current-user IPC
  v
CadPlot AutoCAD bundle -> licensed AutoCAD
```

The existing `cadplot-mcp` stdio server and `cadplot-mcp-http` loopback server are not public
deployment components and must not be placed behind a generic reverse proxy.

## Delivery phases

### Phase 1: read-only vertical slice

- OAuth-protected Streamable HTTP MCP endpoint
- tenant/user/workstation/project authorization
- outbound worker task leasing and revocation
- opaque project and drawing identifiers
- environment validation, scan, inspect, dry-run planning, and operation status
- bounded, path-free responses and audit events

Publishing is forced off in this phase even when a local workstation has publishing enabled.

All seven tools are CAD- and user-file-read-only, but only `list_workstations` is a pure read under
MCP annotation semantics. The queue and operation-status tools persist private durable operation,
dispatch, derived catalog, or deadline state and therefore advertise internal state writes. CadPlot
is model-agnostic: it neither bundles nor selects an AI model; the connected MCP host/client does.

### Phase 2: approval-gated publishing

- browser-visible, single-use stage approval bound to an exact plan
- single-use queue approval bound to an exact plan and manifest digest
- worker-side mutation journal and crash reconciliation
- exact status, receipt, audit, and Pending-only cancellation
- completion only after source and output verification

This phase cannot open publicly until licensed AutoCAD 2016 and 2025 acceptance and recovery runs
have passed on supported release artifacts.

### Phase 3: optional artifact delivery and public review

- opt-in, receipt-bound PDF transfer with short retention
- production rate limits, quotas, monitoring, backups, and incident procedures
- independent security review and tenant-isolation test suite
- verified website, support, privacy, terms, domain, publisher identity, demo, and review account
- OpenAI tool scan, five positive and three negative review cases, submission, and approval

## Production configuration contract

The gateway must fail to start in production unless all of the following are supplied through a
secret manager or deployment configuration:

- canonical public MCP URL and exact allowed hosts;
- OAuth issuer, protected resource audience, token-introspection or signature-verification setup,
  and required claims;
- durable PostgreSQL connection with tenant isolation enabled;
- device-enrollment issuer and key-rotation material;
- append-only audit sink and retention policy;
- rate-limit and operation-expiry policies;
- public support, privacy, terms, and status contacts.

Development-only in-memory stores or static tokens must never be selectable in production.

## Public data contract

Public inputs and outputs may contain opaque workstation, project, drawing, operation, plan, and job
identifiers plus bounded user-facing labels and CAD-derived summaries. They may not contain:

- drive-letter, UNC, device, manifest, configuration, workspace, or template paths;
- `CADPLOT_*` environment variables, pipe names, COM ProgIDs, hostnames, or usernames;
- raw exception messages, stack traces, bearer/device credentials, or database identifiers;
- DWG/DWT content or office plot resources.

The worker resolves identifiers only after tenant/device checks and re-validates the resulting file
with the local `PathPolicy`. A guessed identifier returns a generic not-found response.

## License boundary

CadPlot's source is MIT licensed. Autodesk software, SDK assemblies, trademarks, and subscriptions
are not included. Every workstation operator must install and use a compatible, separately licensed
AutoCAD product under Autodesk's terms. Enrollment proves only that the workstation is registered;
it does not grant or certify an Autodesk license, and the public listing must say so explicitly.

## Current status

The repository now contains the phase-1 gateway composition, seven-tool MCP facade, OAuth token
introspection, tenant-bound PostgreSQL repositories and RLS migrations, signed worker routes,
replay-safe durable control records, shared closed protocol package, and Windows worker CLI. Local
automated tests exercise authentication, cross-tenant denial, leases, dispatch/result signatures,
bounded payloads, path-leak rejection, and retry behavior.

That implementation is not a live deployment. The operator must still provision a verified domain
and TLS edge, an OAuth identity provider and client, hosted PostgreSQL, edge rate limiting and audit
retention, device enrollment/revocation, and secret management. The database migrations must be
applied by a privileged deployment identity; the application connects through the constrained
`cadplot_gateway_runtime` role. Configure the exact `CADPLOT_GATEWAY_*` values and mount the
Ed25519 dispatch private key outside the image. Build only from an immutable
`CADPLOT_PYTHON_IMAGE` digest recorded in provenance.

Do not advertise or submit the service until a registered worker passes a licensed AutoCAD 2016
and 2025 acceptance run and the public support, privacy, terms, reviewer-account, security-scan,
and OpenAI approval gates have executable evidence. Do not enable public publishing until every
phase-2 gate above is complete. The authoritative local readiness fields remain false until the
licensed acceptance runs pass.
