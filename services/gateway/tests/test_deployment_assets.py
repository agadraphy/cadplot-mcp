from __future__ import annotations

import re
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPOSITORY_ROOT / "services" / "gateway" / "Dockerfile"
BLUEPRINT = REPOSITORY_ROOT / "render.yaml"
DEPLOYMENT_GUIDE = REPOSITORY_ROOT / "deploy" / "render" / "README.md"


def test_gateway_dockerfile_bootstraps_uv_from_verified_immutable_image() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    uv_source = re.search(
        r"(?m)^FROM "
        r"(ghcr\.io/astral-sh/uv:[^\s@]+@sha256:[0-9a-f]{64}) AS cadplot_uv$",
        dockerfile,
    )
    assert uv_source is not None
    assert ":latest" not in uv_source.group(1)
    assert "FROM ${CADPLOT_PYTHON_IMAGE}" in dockerfile
    assert "COPY --from=cadplot_uv /uv /uvx /usr/local/bin/" in dockerfile
    assert 'io.cadplot.build.uv-image="' + uv_source.group(1) + '"' in dockerfile
    assert 'io.cadplot.build.python-image="${CADPLOT_PYTHON_IMAGE}"' in dockerfile
    assert "CADPLOT_PYTHON_IMAGE must be an immutable sha256 reference" in dockerfile
    assert r"@sha256:[0-9a-f]{64}" in dockerfile
    assert "UV_PYTHON_DOWNLOADS=0" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable --project services/gateway" in dockerfile
    assert "EXPOSE 10000" in dockerfile


def test_gateway_container_can_read_render_runtime_secret_files() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "getent group 1000" in dockerfile
    assert '--groups "$(getent group 1000 | cut -d: -f1)"' in dockerfile
    assert "USER 10001:10001" in dockerfile


def test_render_blueprint_uses_repository_context_and_no_inline_secrets() -> None:
    document = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))

    assert set(document) == {"services"}
    assert len(document["services"]) == 1
    service = document["services"][0]
    assert service["type"] == "web"
    assert service["runtime"] == "docker"
    assert service["plan"] == "free"
    assert service["dockerfilePath"] == "./services/gateway/Dockerfile"
    assert service["dockerContext"] == "."
    assert service["autoDeployTrigger"] == "checksPass"
    assert service["healthCheckPath"] == "/readyz"
    assert "rootDir" not in service
    assert "preDeployCommand" not in service

    variables = {item["key"]: item for item in service["envVars"]}
    assert len(variables) == len(service["envVars"])
    assert "CADPLOT_GATEWAY_MIGRATION_DATABASE_URL" not in variables

    prompted_values = {
        "CADPLOT_PYTHON_IMAGE",
        "CADPLOT_GATEWAY_PUBLIC_MCP_URL",
        "CADPLOT_GATEWAY_ISSUER_URL",
        "CADPLOT_GATEWAY_INTROSPECTION_URL",
        "CADPLOT_GATEWAY_INTROSPECTION_CLIENT_ID",
        "CADPLOT_GATEWAY_INTROSPECTION_CLIENT_SECRET",
        "CADPLOT_GATEWAY_RESOURCE_AUDIENCE",
        "CADPLOT_GATEWAY_AUTHORIZATION_SCOPE",
        "CADPLOT_GATEWAY_TENANT_CLAIM",
        "CADPLOT_GATEWAY_DATABASE_URL",
        "CADPLOT_GATEWAY_ALLOWED_HOSTS",
    }
    for key in prompted_values:
        assert variables[key] == {"key": key, "sync": False}

    assert variables["CADPLOT_GATEWAY_PRINCIPAL_PEPPER"] == {
        "key": "CADPLOT_GATEWAY_PRINCIPAL_PEPPER",
        "generateValue": True,
    }
    assert variables["CADPLOT_GATEWAY_ENVIRONMENT"]["value"] == "production"
    assert variables["CADPLOT_GATEWAY_HOST"]["value"] == "0.0.0.0"
    assert variables["CADPLOT_GATEWAY_PORT"]["value"] == "10000"
    assert variables["CADPLOT_GATEWAY_DATABASE_POOL_TIMEOUT_SECONDS"]["value"] == "3"
    assert variables["CADPLOT_GATEWAY_GATEWAY_DISPATCH_PRIVATE_KEY_FILE"]["value"] == (
        "/etc/secrets/cadplot_gateway_dispatch_private_key"
    )


def test_migration_guide_documents_transaction_scoped_lock_connection() -> None:
    guide = DEPLOYMENT_GUIDE.read_text(encoding="utf-8")

    assert "two database connections" in guide
    assert "transaction-scoped advisory lock" in guide
    assert "transaction-pooled endpoints" in guide
    assert "migration_lock_timeout" in guide
