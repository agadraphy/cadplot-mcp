from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from typing import TypeVar

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from cadplot_gateway.auth import IntrospectionTokenVerifier, principal_from_access_token
from cadplot_gateway.models import (
    CreatePublishPlanRequest,
    DrawingId,
    EnqueueListProjectsRequest,
    InspectDrawingRequest,
    ListWorkstationsOutput,
    OperationId,
    OperationView,
    PrincipalContext,
    ProjectId,
    ScanDrawingsRequest,
    Scope,
    ValidateEnvironmentRequest,
    WorkstationId,
)
from cadplot_gateway.redaction import UnsafePublicPayload, assert_public_payload_safe
from cadplot_gateway.service import GatewayError, GatewayService
from cadplot_gateway.settings import GatewaySettings

SERVER_INSTRUCTIONS = (
    "Use only registered workstations and opaque project/drawing IDs. Start with list_workstations "
    "and list_projects. All current tools are CAD-read-only and create_publish_plan is a dry run "
    "that never plots. Queueing and deadline materialization write only private durable operation "
    "state. Poll get_operation when work is pending. Never request or reveal local paths."
)
PURE_READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
INTERNAL_STATE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
_OutputT = TypeVar("_OutputT", bound=BaseModel)
ServiceResolver = Callable[[PrincipalContext], GatewayService]


def _guard_public_output(value: _OutputT) -> _OutputT:
    """Apply a fail-closed second egress check at the public MCP boundary."""

    try:
        assert_public_payload_safe(value.model_dump(mode="json"))
    except UnsafePublicPayload:
        raise GatewayError("unsafe_public_output") from None
    return value


def _stable_idempotency_key(
    *,
    principal: PrincipalContext,
    action: str,
    request_id: str,
    access_token: str,
    pepper: str,
) -> str:
    """Derive a retry-stable opaque key without persisting the bearer token."""

    if not request_id or len(request_id) > 4096 or not access_token or len(access_token) > 8192:
        raise GatewayError("invalid_request_identity")
    material = json.dumps(
        {
            "action": action,
            "client_id": principal.client_id,
            "request_id": request_id,
            "subject_id": principal.subject_id,
            "tenant_id": principal.tenant_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    token_key = hmac.new(
        pepper.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    digest = hmac.new(token_key, material, hashlib.sha256).digest()
    return f"idem_{uuid.UUID(bytes=digest[:16], version=4)}"


def build_mcp_server(
    settings: GatewaySettings,
    service: GatewayService | None = None,
    *,
    service_resolver: ServiceResolver | None = None,
    token_verifier: TokenVerifier | None = None,
) -> MCPServer:
    """Build the seven-tool, OAuth-protected public read-only facade."""

    if (service is None) == (service_resolver is None):
        raise ValueError("configure exactly one gateway service source")

    def resolve_service(caller: PrincipalContext) -> GatewayService:
        if service is not None:
            resolved = service
        else:
            assert service_resolver is not None
            resolved = service_resolver(caller)
        if not isinstance(resolved, GatewayService):
            raise GatewayError("service_unavailable")
        return resolved

    verifier = token_verifier or IntrospectionTokenVerifier(settings)
    server = MCPServer(
        "CadPlot",
        instructions=SERVER_INSTRUCTIONS,
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=settings.issuer_url,
            resource_server_url=settings.public_mcp_url,
            required_scopes=[Scope.READ.value],
        ),
    )

    def principal() -> PrincipalContext:
        verified = principal_from_access_token(
            get_access_token(),
            issuer=str(settings.issuer_url),
            pepper=settings.principal_pepper.get_secret_value(),
        )
        scopes = frozenset(scope for scope in Scope if scope.value in verified.scopes)
        return PrincipalContext.from_auth_adapter(
            tenant_id=verified.tenant_id,
            subject_id=verified.user_id,
            client_id=verified.client_id,
            scopes=scopes,
        )

    def idempotency_key(caller: PrincipalContext, context: Context, action: str) -> str:
        access_token = get_access_token()
        if access_token is None:
            raise GatewayError("authentication_required")
        return _stable_idempotency_key(
            principal=caller,
            action=action,
            request_id=context.request_id,
            access_token=access_token.token,
            pepper=settings.principal_pepper.get_secret_value(),
        )

    @server.tool(title="List registered workstations", annotations=PURE_READ)
    def list_workstations() -> ListWorkstationsOutput:
        """List only the caller's registered CadPlot workstations and online state."""
        caller = principal()
        return _guard_public_output(resolve_service(caller).list_workstations(caller))

    @server.tool(title="List approved projects", annotations=INTERNAL_STATE_WRITE)
    def list_projects(workstation_id: WorkstationId, ctx: Context) -> OperationView:
        """Queue discovery of approved project aliases; never returns workstation paths."""
        caller = principal()
        return _guard_public_output(
            resolve_service(caller).enqueue_list_projects(
                caller,
                EnqueueListProjectsRequest(
                    workstation_id=workstation_id,
                    idempotency_key=idempotency_key(caller, ctx, "list_projects"),
                ),
            )
        )

    @server.tool(title="Validate workstation environment", annotations=INTERNAL_STATE_WRITE)
    def validate_environment(workstation_id: WorkstationId, ctx: Context) -> OperationView:
        """Queue a sanitized read-only readiness check on one registered workstation."""
        caller = principal()
        return _guard_public_output(
            resolve_service(caller).enqueue_validate_environment(
                caller,
                ValidateEnvironmentRequest(
                    workstation_id=workstation_id,
                    idempotency_key=idempotency_key(caller, ctx, "validate_environment"),
                ),
            )
        )

    @server.tool(title="Scan approved drawings", annotations=INTERNAL_STATE_WRITE)
    def scan_drawings(
        project_id: ProjectId,
        ctx: Context,
        recursive: bool = True,
        limit: int = 100,
    ) -> OperationView:
        """Queue bounded DWG discovery inside one approved opaque project reference."""
        caller = principal()
        return _guard_public_output(
            resolve_service(caller).enqueue_scan_drawings(
                caller,
                ScanDrawingsRequest(
                    project_id=project_id,
                    recursive=recursive,
                    limit=limit,
                    idempotency_key=idempotency_key(caller, ctx, "scan_drawings"),
                ),
            )
        )

    @server.tool(title="Inspect approved drawing", annotations=INTERNAL_STATE_WRITE)
    def inspect_drawing(drawing_id: DrawingId, ctx: Context) -> OperationView:
        """Queue a read-only inspection by opaque drawing ID; never opens arbitrary paths."""
        caller = principal()
        return _guard_public_output(
            resolve_service(caller).enqueue_inspect_drawing(
                caller,
                InspectDrawingRequest(
                    drawing_id=drawing_id,
                    idempotency_key=idempotency_key(caller, ctx, "inspect_drawing"),
                ),
            )
        )

    @server.tool(title="Create dry-run publish plan", annotations=INTERNAL_STATE_WRITE)
    def create_publish_plan(drawing_id: DrawingId, ctx: Context) -> OperationView:
        """Queue deterministic planning for one approved drawing; never stages or plots."""
        caller = principal()
        return _guard_public_output(
            resolve_service(caller).enqueue_create_publish_plan(
                caller,
                CreatePublishPlanRequest(
                    drawing_id=drawing_id,
                    idempotency_key=idempotency_key(caller, ctx, "create_publish_plan"),
                ),
            )
        )

    @server.tool(title="Get CadPlot operation", annotations=INTERNAL_STATE_WRITE)
    def get_operation(operation_id: OperationId) -> OperationView:
        """Read one caller-owned asynchronous operation and its sanitized result."""
        caller = principal()
        return _guard_public_output(resolve_service(caller).get_operation(caller, operation_id))

    return server


def build_transport_security(settings: GatewaySettings) -> TransportSecuritySettings:
    hosts: list[str] = []
    for host in settings.allowed_hosts:
        hosts.extend((host, f"{host}:443"))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[],
    )
