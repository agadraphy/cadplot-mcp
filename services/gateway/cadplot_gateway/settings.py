from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Fail-closed process configuration for the public resource server."""

    model_config = SettingsConfigDict(
        env_prefix="CADPLOT_GATEWAY_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = "production"
    public_mcp_url: AnyHttpUrl
    issuer_url: AnyHttpUrl
    introspection_url: AnyHttpUrl
    introspection_client_id: str = Field(min_length=1, max_length=256)
    introspection_client_secret: SecretStr
    resource_audience: str = Field(min_length=1, max_length=512)
    authorization_scope: str = Field(
        default="cadplot.read",
        pattern=r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$",
    )
    tenant_claim: str = Field(default="organization_id", pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    principal_pepper: SecretStr
    database_url: SecretStr
    allowed_hosts: tuple[str, ...] = Field(min_length=1, max_length=16)
    host: str = "0.0.0.0"  # noqa: S104 - container listener; TLS terminates at the edge.
    port: int = Field(default=8000, ge=1024, le=65535)
    max_request_body_bytes: int = Field(default=1024 * 1024, ge=16_384, le=1024 * 1024)
    token_response_bytes: int = Field(default=64 * 1024, ge=1024, le=256 * 1024)
    token_clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    introspection_max_concurrency: int = Field(default=32, ge=1, le=256)
    introspection_queue_timeout_ms: int = Field(default=250, ge=1, le=5_000)
    operation_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    max_active_operations_per_client: int = Field(default=100, ge=1, le=10_000)
    database_pool_min_size: int = Field(default=2, ge=1, le=32)
    database_pool_max_size: int = Field(default=16, ge=1, le=128)
    database_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    worker_lease_seconds: int = Field(default=30, ge=5, le=60)
    worker_policy_version: int = Field(default=1, ge=1, le=2_147_483_647)
    gateway_dispatch_private_key_file: Path | None = None

    @field_validator("allowed_hosts")
    @classmethod
    def validate_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            host = value.strip().lower()
            if (
                not host
                or "/" in host
                or "\\" in host
                or "://" in host
                or "*" in host
                or host in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("allowed_hosts must contain exact public hostnames")
            cleaned.append(host)
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("allowed_hosts must be unique")
        return tuple(cleaned)

    @model_validator(mode="after")
    def validate_public_boundary(self) -> GatewaySettings:
        public = urlsplit(str(self.public_mcp_url))
        issuer = urlsplit(str(self.issuer_url))
        introspection = urlsplit(str(self.introspection_url))
        if public.path.rstrip("/") != "/mcp" or public.query or public.fragment:
            raise ValueError("public_mcp_url must be the canonical /mcp URL")
        if public.hostname is None or public.hostname.lower() not in self.allowed_hosts:
            raise ValueError("public_mcp_url host must be explicitly allowed")
        if self.environment == "production":
            for label, parsed in (
                ("public_mcp_url", public),
                ("issuer_url", issuer),
                ("introspection_url", introspection),
            ):
                if parsed.scheme != "https":
                    raise ValueError(f"{label} must use HTTPS in production")
                if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
                    raise ValueError(f"{label} must not use a loopback host in production")
            if len(self.introspection_client_secret.get_secret_value()) < 24:
                raise ValueError("introspection_client_secret is too short for production")
            if len(self.principal_pepper.get_secret_value()) < 32:
                raise ValueError("principal_pepper is too short for production")
            self._validate_production_database_url(self.database_url.get_secret_value())
            if self.database_pool_min_size < 2 or self.database_pool_max_size < 2:
                raise ValueError("production database pool must keep two connections available")
        if self.database_pool_min_size > self.database_pool_max_size:
            raise ValueError("database_pool_min_size must not exceed database_pool_max_size")
        if (
            self.gateway_dispatch_private_key_file is not None
            and not self.gateway_dispatch_private_key_file.is_absolute()
        ):
            raise ValueError("gateway_dispatch_private_key_file must be absolute")
        return self

    @staticmethod
    def _validate_production_database_url(value: str) -> None:
        """Require authenticated TLS for every production PostgreSQL TCP endpoint."""

        try:
            database = urlsplit(value)
            query = parse_qsl(database.query, keep_blank_values=True, strict_parsing=True)
            hostname = unquote(database.hostname or "")
        except (TypeError, ValueError):
            raise ValueError("production database_url is invalid") from None

        if database.scheme != "postgresql" or not hostname:
            raise ValueError("production database_url must use PostgreSQL TCP")
        if any(separator in hostname for separator in ("/", "\\")) or any(
            key == "host" for key, _ in query
        ):
            # Unix-domain sockets and query-string host overrides make the actual transport
            # ambiguous. Production uses an explicit TCP authority and certificate hostname.
            raise ValueError("production database_url must use PostgreSQL TCP")
        ssl_modes = tuple(value for key, value in query if key == "sslmode")
        if ssl_modes != ("verify-full",):
            raise ValueError("production database_url must set sslmode=verify-full")
