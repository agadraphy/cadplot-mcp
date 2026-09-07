# Render deployment scaffold

The repository-root `render.yaml` defines one Docker web service for the public gateway. It is a
safe bootstrap scaffold, not evidence of a production deployment. It deliberately creates no
database, domain, OAuth client, workstation enrollment, or paid service.

## What the scaffold guarantees

- The Docker context remains the repository root so `packages/protocol` is available to the
  gateway build. Do not set Render `rootDir` to `services/gateway`.
- The gateway listens on `0.0.0.0:10000`, Render's default web-service port.
- Auto-deploy waits for linked-repository CI checks. A service connected only as a public Git URL
  does not support this auto-deploy mode.
- The initial plan is `free` to avoid an implicit paid resource. It is suitable only for deployment
  rehearsal; select an explicitly approved always-on plan before claiming public availability.
- Secret and deployment-specific values are represented by `sync: false`; no example credential
  or made-up public hostname is committed.
- `CADPLOT_GATEWAY_PRINCIPAL_PEPPER` is generated once by Render instead of being stored in Git.

The Blueprint probes `/readyz`, the bounded, secret-free readiness route. It returns success only
when the process can obtain a constrained runtime database connection and sees the expected schema
migration. `/healthz` remains the process-only liveness route; `/mcp` is not suitable for platform
health checks because it is deliberately authenticated. Database-pool acquisition is capped at
three seconds so the readiness probe can respond within Render's five-second HTTP health-check
window under normal database-query latency. The application admits only one isolated readiness
probe at a time, briefly caches its result, and requires a production pool with at least two
connections so public probes cannot consume all capacity needed by protected requests.

## Immutable container inputs

The Dockerfile copies `uv` from Astral's official distroless image, pinned to both version `0.12.10`
and its verified multi-platform digest. The image's GitHub attestation can be rechecked with:

```console
gh attestation verify --owner astral-sh \
  oci://ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500
```

Choose the approved Python base independently and supply it as a complete, lowercase digest
reference, for example the general form `registry/repository:version@sha256:<64 hex digits>`. The
Dockerfile rejects tag-only values and records the exact Python and uv source references as OCI
labels. Record the same references in the release SBOM/provenance evidence.

Build from the repository root without passing application secrets:

```console
docker build \
  --build-arg CADPLOT_PYTHON_IMAGE=<verified-python-reference> \
  --file services/gateway/Dockerfile \
  .
```

Render makes service environment variables available as Docker build arguments. The Dockerfile
declares only the non-secret `CADPLOT_PYTHON_IMAGE` build argument; do not add gateway credentials
as Docker `ARG` instructions.

## Provisioning sequence

1. Provision an OAuth 2.1 provider/client, public domain, and PostgreSQL separately. If Render is
   used for the database, create and price it explicitly rather than adding it silently to this
   Blueprint.
2. Run `cadplot-gateway-migrate` with only
   `CADPLOT_GATEWAY_MIGRATION_DATABASE_URL` available to a separate, short-lived deployment job.
   The runner validates and hash-records the complete packaged migration sequence and bounds
   advisory-lock contention to ten seconds before returning `migration_lock_timeout`. Its
   privileged PostgreSQL URL must use `sslmode=verify-full` and must not equal the runtime URL.
   The job opens two database connections: one owns a transaction-scoped advisory lock for the
   complete run while the other commits migrations separately. Direct, session-pooled, and
   transaction-pooled endpoints are supported when at least two concurrent connections are
   available. Create a distinct `NOSUPERUSER NOBYPASSRLS` runtime login, grant it membership in
   `cadplot_gateway_runtime`, and make sure it owns no gateway objects.
3. In Render, create a Blueprint from `render.yaml` through the connected GitHub repository. Review
   the region and plan before creating anything.
4. Fill every `sync: false` value during the initial Blueprint flow. Render ignores new
   `sync: false` entries added during later syncs, so add later values manually in the dashboard.
5. Add a Render runtime secret file named `cadplot_gateway_dispatch_private_key`. Its contents must
   be the production Ed25519 dispatch private key in the format accepted by the gateway. Render
   mounts it at `/etc/secrets/cadplot_gateway_dispatch_private_key`; the container user belongs to
   Render's secret-file group (`gid 1000`) for read access.
6. Deploy only after the values below satisfy the exact constraints, then inspect startup and edge
   logs. Fail-closed startup before the database, key, and OAuth configuration are ready is expected.
7. Register a licensed Windows worker and complete the documented AutoCAD acceptance runs before
   advertising the endpoint or submitting it for public review.

Although Render supports `preDeployCommand`, this Blueprint intentionally does not use it. A
pre-deploy command on the web service reads the service's environment, which would leave the
privileged migration credential available to the long-running gateway container as well. Keep the
migration URL out of the web-service environment and run the migration command from an isolated
deployment job or trusted operator environment; remove the credential when that job exits.

## Values Render asks for

| Variable | Required value |
| --- | --- |
| `CADPLOT_PYTHON_IMAGE` | Verified immutable Python image reference ending in `@sha256:` plus 64 lowercase hex digits. |
| `CADPLOT_GATEWAY_PUBLIC_MCP_URL` | Canonical final HTTPS URL ending exactly in `/mcp`. |
| `CADPLOT_GATEWAY_RESOURCE_AUDIENCE` | The same canonical MCP URL. |
| `CADPLOT_GATEWAY_ALLOWED_HOSTS` | JSON array containing only the exact final public hostname. |
| `CADPLOT_GATEWAY_ISSUER_URL` | Production HTTPS OAuth issuer URL. |
| `CADPLOT_GATEWAY_INTROSPECTION_URL` | Production HTTPS token-introspection URL. |
| `CADPLOT_GATEWAY_INTROSPECTION_CLIENT_ID` | OAuth introspection client identifier. |
| `CADPLOT_GATEWAY_INTROSPECTION_CLIENT_SECRET` | OAuth introspection client secret. |
| `CADPLOT_GATEWAY_DATABASE_URL` | Runtime login URL over PostgreSQL TCP, with exactly one `sslmode=verify-full` query value. |

When the final domain changes, update the public URL, resource audience, and allowed-host JSON
together. Do not insert the Render service URL automatically: Render Blueprint files do not support
general string interpolation, and the gateway requires these security-bound values to match exactly.

## Official references

- [Render Blueprint YAML reference](https://render.com/docs/blueprint-spec)
- [Render monorepo support](https://render.com/docs/monorepo-support)
- [Render Docker builds and build arguments](https://render.com/docs/docker)
- [Render web-service port binding](https://render.com/docs/web-services#port-binding)
- [Render health checks](https://render.com/docs/health-checks)
- [Astral: using uv in Docker](https://docs.astral.sh/uv/guides/integration/docker/)
