# CadPlot gateway

This directory is the cloud-side CadPlot MCP resource server. It is intentionally packaged apart
from the workstation project and must never contain or import AutoCAD, Autodesk SDK assemblies,
`pywin32`, local DWG paths, named-pipe code, or company plot resources.

The gateway installs the exact `cadplot-protocol` release; the standalone workstation wheel bundles
that release's same source package so its existing offline release kit remains self-contained. The
two sides retain separate MCP runtimes and compatibility tests enforce the wire contract. Build the
container from the repository root only after selecting a verified immutable base image:

```powershell
$env:CADPLOT_PYTHON_IMAGE = "python:3.12-slim@sha256:<verified-digest>"
docker build --build-arg "CADPLOT_PYTHON_IMAGE=$env:CADPLOT_PYTHON_IMAGE" `
  -f services/gateway/Dockerfile .
```

Replace the quoted placeholder with the exact digest recorded in release provenance. A
`services/gateway`-only build context is rejected by design because it omits the shared protocol
artifact.

The first implementation increment exposes only CAD- and user-file-read-only operations through
opaque workstation, project, drawing, and operation identifiers. Only `list_workstations` is a
pure read under MCP annotation semantics; queue/status tools persist private durable operation or
catalog state and advertise that accurately. CadPlot is model-agnostic, and the connected MCP
host/client selects the compatible AI model. Publishing remains disabled until the approval,
mutation-journal, crash-reconciliation, licensed acceptance, and public-review gates documented in
[`docs/public-deployment.md`](../../docs/public-deployment.md) are complete.

## Live endpoint

The production beta gateway is available at
`https://cadplot-mcp-gateway.onrender.com/mcp`. It advertises OAuth protected-resource metadata,
uses ZITADEL Dynamic Client Registration with PKCE, and allows new users to self-register. A
connected client can discover and authenticate to the MCP endpoint without receiving a shared
client secret from the operator.

The gateway being live does not make AutoCAD itself public. Tool execution that needs a drawing
still depends on an enrolled outbound Windows worker running a separately licensed AutoCAD
installation. The Render free tier may cold-start after inactivity.

## Security posture

- OAuth token introspection is fail-closed and audience-bound.
- The tenant and user are derived from verified token claims, never from tool inputs.
- Production configuration requires HTTPS, an exact host allowlist, PostgreSQL, and non-placeholder
  credentials.
- Worker results pass a second egress guard that rejects local path/control fields and path-shaped
  values.
- In-memory repositories, when present, are test/development adapters and cannot be selected by a
  production startup.

## Deployment boundary

The production entry point is wired to the OAuth MCP facade, signed worker routes, and the durable
PostgreSQL repositories. Migrations `0001` through `0004` create the tenant-isolated operation,
dispatch, replay, workstation-key, and request-proof stores. Apply them with a privileged deployer;
the runtime process sets the exact `cadplot_gateway_runtime` role and must not own schema objects.

Production requires the `CADPLOT_GATEWAY_*` settings declared in `settings.py`, including the
canonical `/mcp` URL, exact allowed host list, OAuth introspection credentials and audience,
principal pepper, a PostgreSQL TCP URL with exactly `sslmode=verify-full`, and an absolute
secret-mounted Ed25519 dispatch private-key file. For example, inject
`CADPLOT_GATEWAY_DATABASE_URL=postgresql://gateway@db.example/cadplot?sslmode=verify-full` from the
deployment secret store; production rejects Unix-socket targets, host query overrides, and weaker
TLS modes.
The Docker build deliberately has no default base image. Supply `CADPLOT_PYTHON_IMAGE` as an
immutable, verified image digest only after recording it in release SBOM and provenance evidence.

Apply the packaged migrations with a separate, short-lived privileged identity before starting
the web service:

```console
CADPLOT_GATEWAY_MIGRATION_DATABASE_URL=<privileged-verify-full-url> \
  cadplot-gateway-migrate
```

The migration job opens two database connections using that URL. One keeps a transaction-scoped
advisory lock for the complete run while the other commits each migration separately; this remains
safe with direct PostgreSQL, session pooling, and transaction pooling. The deployment database
endpoint must therefore permit at least two concurrent connections for the job.

The migration runner retries the nonblocking lock for at most ten seconds before returning
`migration_lock_timeout`, rejects an unmanaged pre-existing schema, and records the exact filename
and SHA-256 of each migration in `cadplot_gateway.schema_migrations`. Never expose the privileged
migration URL to the long-running gateway process. The runtime database login must be a distinct
`NOSUPERUSER`, `NOBYPASSRLS`, `NOINHERIT` login that owns no gateway objects and can only `SET ROLE
cadplot_gateway_runtime`; pooled connections verify that boundary and fail closed.

Production exposes `GET /healthz` for process liveness and `GET /readyz` for secret-free readiness.
Readiness admits at most one isolated database probe, briefly caches its result, and fails excess
probes closed. Production keeps at least two database connections available so anonymous health
traffic cannot consume the entire runtime pool. A probe succeeds only when the constrained runtime
role can reach PostgreSQL and the latest hash-bound schema migration is present. The MCP endpoint
itself remains OAuth protected. A valid,
fresh, non-replayed signed worker request refreshes that device's bounded presence window; an old
static `online` flag can no longer authorize a lease.

The repository-root `render.yaml` and [`deploy/render/README.md`](../../deploy/render/README.md)
remain secret-free deployment definitions. The live beta supplies the HTTPS edge, OAuth client and
claims, and hosted database outside Git. Device enrollment/revocation and licensed Windows-worker
acceptance remain operator responsibilities, and approval-gated CAD publishing remains disabled
from the public gateway.
