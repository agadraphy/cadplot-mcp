from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from cadplot_gateway.settings import GatewaySettings


def _settings(**overrides: object) -> GatewaySettings:
    values: dict[str, object] = {
        "environment": "production",
        "public_mcp_url": "https://mcp.cadplot.test/mcp",
        "issuer_url": "https://identity.cadplot.test",
        "introspection_url": "https://identity.cadplot.test/oauth/introspect",
        "introspection_client_id": "cadplot-gateway",
        "introspection_client_secret": SecretStr("i" * 32),
        "resource_audience": "https://mcp.cadplot.test/mcp",
        "principal_pepper": SecretStr("p" * 32),
        "database_url": SecretStr("postgresql://gateway@db/cadplot?sslmode=verify-full"),
        "allowed_hosts": ("mcp.cadplot.test",),
    }
    values.update(overrides)
    return GatewaySettings(**values)


def test_production_settings_accept_exact_https_boundary() -> None:
    settings = _settings()

    assert str(settings.public_mcp_url).rstrip("/") == "https://mcp.cadplot.test/mcp"
    assert settings.resource_audience == "https://mcp.cadplot.test/mcp"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("public_mcp_url", "http://mcp.cadplot.test/mcp"),
        ("public_mcp_url", "https://mcp.cadplot.test/other"),
        ("issuer_url", "http://identity.cadplot.test"),
        ("introspection_url", "http://identity.cadplot.test/oauth/introspect"),
        ("database_url", SecretStr("sqlite:///gateway.db")),
        ("resource_audience", "https://other.cadplot.test/mcp"),
    ],
)
def test_production_settings_reject_boundary_mismatch(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def test_allowed_hosts_reject_wildcard_and_loopback() -> None:
    with pytest.raises(ValidationError):
        _settings(allowed_hosts=("*.cadplot.test",))
    with pytest.raises(ValidationError):
        _settings(
            public_mcp_url="https://localhost/mcp",
            resource_audience="https://localhost/mcp",
            allowed_hosts=("localhost",),
        )


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://gateway@db/cadplot",
        "postgresql://gateway@db/cadplot?sslmode=disable",
        "postgresql://gateway@db/cadplot?sslmode=prefer",
        "postgresql://gateway@db/cadplot?sslmode=require",
        "postgresql://gateway@db/cadplot?sslmode=verify-ca",
        "postgresql://gateway@db/cadplot?sslmode=VERIFY-FULL",
        ("postgresql://gateway@db/cadplot?sslmode=verify-full&sslmode=disable"),
    ],
)
def test_production_tcp_database_rejects_missing_or_weaker_tls(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError):
        _settings(database_url=SecretStr(database_url))


@pytest.mark.parametrize(
    "database_url",
    [
        ("postgresql:///cadplot?host=%2Fvar%2Frun%2Fpostgresql&sslmode=verify-full"),
        "postgresql://%2Fvar%2Frun%2Fpostgresql/cadplot?sslmode=verify-full",
        "postgresql://db,%2Fvar%2Frun%2Fpostgresql/cadplot?sslmode=verify-full",
        ("postgresql://gateway@db/cadplot?host=%2Fvar%2Frun%2Fpostgresql&sslmode=verify-full"),
    ],
)
def test_production_database_rejects_unix_socket_and_host_override(
    database_url: str,
) -> None:
    with pytest.raises(ValidationError):
        _settings(database_url=SecretStr(database_url))


def test_production_database_accepts_verify_full_with_certificate_options() -> None:
    settings = _settings(
        database_url=SecretStr(
            "postgresql://gateway@db.cadplot.test/cadplot?"
            "sslrootcert=%2Fetc%2Fssl%2Fcadplot-ca.pem&sslmode=verify-full"
        )
    )

    assert "verify-full" in settings.database_url.get_secret_value()


def test_database_tls_validation_never_exposes_password() -> None:
    password = "do-not-log-this-password"
    with pytest.raises(ValidationError) as captured:
        _settings(database_url=(f"postgresql://gateway:{password}@db/cadplot?sslmode=require"))

    rendered = str(captured.value)
    assert password not in rendered
    assert "gateway:" not in rendered


def test_nonproduction_database_can_use_local_transport_without_tls() -> None:
    settings = _settings(
        environment="test",
        database_url=SecretStr("postgresql:///cadplot?host=%2Fvar%2Frun%2Fpostgresql"),
    )

    assert settings.environment == "test"


def test_dispatch_key_path_must_be_absolute_when_configured(tmp_path: Path) -> None:
    configured = _settings(gateway_dispatch_private_key_file=tmp_path / "dispatch.pem")
    assert configured.gateway_dispatch_private_key_file == tmp_path / "dispatch.pem"

    with pytest.raises(ValidationError):
        _believed_relative = Path("relative-dispatch.pem")
        _settings(gateway_dispatch_private_key_file=_believed_relative)
