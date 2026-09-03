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
PostgreSQL repositories. Migrations `0001` through `0003` create the tenant-isolated operation,
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

The code is deployable, but this repository is not evidence of a live public service. A real
domain/TLS edge, OAuth client and claims, hosted database, rate limiting and audit operations,
device enrollment/revocation, and a licensed Windows worker still have to be provisioned and
verified. Public publishing remains disabled.
