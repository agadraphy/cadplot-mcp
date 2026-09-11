"""CadPlot's authenticated, CAD-read-only public MCP command-line client."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from importlib.metadata import version
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthFlowError
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata

from cadplot_mcp.cli_auth import CLIError, CredentialStore, LoopbackLogin, PersistentOAuthProvider

DEFAULT_SERVER = "https://cadplot-mcp-gateway.onrender.com/mcp"
UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def server_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            and "\\" not in value
        )
        port = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError("Use an HTTPS MCP URL without credentials/query/fragment.")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return urlunsplit(
        (
            "https",
            host + (f":{port}" if port and port != 443 else ""),
            parsed.path or "/mcp",
            "",
            "",
        )
    )


def opaque_id(prefix: str):
    def validate(value: str) -> str:
        if not re.fullmatch(rf"{prefix}_{UUID_PATTERN}", value):
            raise argparse.ArgumentTypeError(f"Expected an opaque {prefix}_ UUID from CadPlot.")
        return value

    return validate


def bounded_int(low: int, high: int):
    def validate(value: str) -> int:
        try:
            result = int(value)
        except ValueError:
            result = 0
        if not low <= result <= high:
            raise argparse.ArgumentTypeError(f"Expected an integer between {low} and {high}.")
        return result

    return validate


def timeout_seconds(value: str) -> float:
    try:
        result = float(value)
    except ValueError:
        result = 0
    if not math.isfinite(result) or not 1 <= result <= 600:
        raise argparse.ArgumentTypeError("Timeout must be between 1 and 600 seconds.")
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cadplot", description=__doc__)
    root.add_argument("--version", action="version", version=f"cadplot {version('cadplot-mcp')}")
    root.add_argument(
        "--server", type=server_url, default=os.getenv("CADPLOT_SERVER", DEFAULT_SERVER)
    )
    root.add_argument(
        "--timeout",
        type=timeout_seconds,
        default=120.0,
        help="request/command budget in seconds (default: 120; login adds 300)",
    )
    root.add_argument("--json", action="store_true", help="compact JSON; errors go to stderr")
    commands = root.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login", help="sign in using your browser (OAuth + PKCE)")
    login.add_argument("--port", type=bounded_int(1024, 65535), default=43817)
    commands.add_parser(
        "logout", help="remove this endpoint's local credentials; no server revocation"
    )
    commands.add_parser("health", help="check public gateway readiness without signing in")
    commands.add_parser("tools", help="list MCP tools exposed to your account")
    workstations = commands.add_parser("workstations").add_subparsers(required=True)
    workstations.add_parser("list").set_defaults(tool="list_workstations", fields=())
    validate = workstations.add_parser("validate")
    validate.add_argument("workstation_id", type=opaque_id("ws"))
    validate.set_defaults(tool="validate_environment", fields=("workstation_id",))
    projects = commands.add_parser("projects").add_subparsers(required=True)
    projects_list = projects.add_parser("list")
    projects_list.add_argument(
        "--workstation", dest="workstation_id", required=True, type=opaque_id("ws")
    )
    projects_list.set_defaults(tool="list_projects", fields=("workstation_id",))
    drawings = commands.add_parser("drawings").add_subparsers(required=True)
    scan = drawings.add_parser("scan")
    scan.add_argument("--project", dest="project_id", required=True, type=opaque_id("prj"))
    scan.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    scan.add_argument("--limit", type=bounded_int(1, 100), default=100)
    scan.set_defaults(tool="scan_drawings", fields=("project_id", "recursive", "limit"))
    inspect = drawings.add_parser("inspect")
    inspect.add_argument("drawing_id", type=opaque_id("drw"))
    inspect.set_defaults(tool="inspect_drawing", fields=("drawing_id",))
    plan = commands.add_parser("plan", help="create a dry-run plan; never publish or change a DWG")
    plan.add_argument("drawing_id", type=opaque_id("drw"))
    plan.add_argument("--dry-run", action="store_true", help="explicit marker; always dry-run")
    plan.set_defaults(tool="create_publish_plan", fields=("drawing_id",))
    operations = commands.add_parser("operations").add_subparsers(required=True)
    status = operations.add_parser("status")
    status.add_argument("operation_id", type=opaque_id("op"))
    status.set_defaults(tool="get_operation", fields=("operation_id",))
    return root


async def require_login(*_):
    raise CLIError(
        "login_required", "Run cadplot login for this server before using this command.", 3
    )


async def require_https(request: httpx.Request) -> None:
    # Includes metadata, registration, refresh and redirects, not only the MCP endpoint.
    if request.url.scheme != "https" or request.url.username or request.url.password:
        raise CLIError("insecure_transport", "Refusing a non-HTTPS or credential-bearing request.")


async def execute(args: argparse.Namespace) -> dict:
    async with AsyncExitStack() as stack:
        if args.command == "health":
            async with httpx.AsyncClient(timeout=args.timeout) as client:
                url = urlsplit(args.server)
                response = await client.get(urlunsplit((url.scheme, url.netloc, "/readyz", "", "")))
                response.raise_for_status()
                if response.json().get("status") != "ready":
                    raise CLIError("gateway_not_ready", "Gateway readiness check did not pass.")
            return {"status": "ready", "server": args.server}

        store = CredentialStore(args.server)
        if args.command == "logout":
            store.clear()
            return {"status": "logged_out", "server": args.server, "server_tokens_revoked": False}
        login = None
        if args.command == "login":
            # Changing the port requires a new DCR client; never silently reuse a different URI.
            login = await stack.enter_async_context(LoopbackLogin(args.port))
            info = await store.get_client_info()
            if info and [str(uri) for uri in info.redirect_uris or []] != [login.redirect_uri]:
                raise CLIError(
                    "login_port_changed", "Run cadplot logout before changing login port.", 3
                )
        elif await store.get_tokens() is None:
            await require_login()

        info = await store.get_client_info()
        redirect_uri = login.redirect_uri if login else "http://127.0.0.1:43817/callback"
        metadata = OAuthClientMetadata(
            redirect_uris=[redirect_uri],
            token_endpoint_auth_method="none",
            client_name="CadPlot CLI",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )
        if info and not login:
            metadata.redirect_uris = info.redirect_uris
        auth = PersistentOAuthProvider(
            args.server,
            client_metadata=metadata,
            storage=store,
            redirect_handler=login.redirect if login else require_login,
            callback_handler=login.callback if login else require_login,
        )
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                auth=auth,
                timeout=args.timeout,
                follow_redirects=True,
                event_hooks={"request": [require_https]},
            )
        )
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(
                args.server,
                http_client=client,
            )
        )
        session = await stack.enter_async_context(
            ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=args.timeout),
            )
        )
        await session.initialize()
        if args.command in ("login", "tools"):
            result = await session.list_tools()
            if args.command == "login":
                return {
                    "status": "authenticated",
                    "server": args.server,
                    "tools": [tool.name for tool in result.tools],
                }
            return result.model_dump(mode="json", exclude_none=True)
        result = await session.call_tool(
            args.tool, {field: getattr(args, field) for field in args.fields}
        )
        if result.isError:
            # Remote errors can contain provider data. Do not echo arbitrary exception text.
            raise CLIError(
                "tool_failed", "MCP rejected the request; verify IDs and workstation access."
            )
        data = result.structuredContent
        if data is None:
            # Older MCP peers may return a single JSON text block instead of structuredContent.
            texts = [block.text for block in result.content if block.type == "text"]
            if len(texts) != 1:
                raise CLIError("invalid_tool_result", "Expected one structured tool result.")
            try:
                data = json.loads(texts[0])
            except ValueError:
                raise CLIError("invalid_tool_result", "Tool result was not valid JSON.") from None
        if not isinstance(data, dict):
            raise CLIError("invalid_tool_result", "Expected a JSON object from the gateway.")
        return data


def public_error(exc: BaseException) -> CLIError:
    if isinstance(exc, BaseExceptionGroup):
        errors = [public_error(child) for child in exc.exceptions]
        return next((error for error in errors if error.code != "request_failed"), errors[0])
    if isinstance(exc, CLIError):
        return exc
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return CLIError("timeout", "Request timed out. A queued operation may still be running.", 5)
    if isinstance(exc, OAuthFlowError):
        return CLIError("authentication_failed", "Sign-in failed; retry cadplot login.", 3)
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (401, 403):
            return CLIError(
                "access_denied", "Sign in with an authorized account using cadplot login.", 3
            )
        return CLIError("http_error", f"Gateway returned HTTP {exc.response.status_code}.")
    return CLIError(
        "request_failed",
        "Request failed. Check connectivity and gateway/worker availability. "
        "Do not blindly resubmit a queue command; its outcome may be unknown.",
    )


async def run(args: argparse.Namespace) -> dict:
    async with asyncio.timeout(args.timeout + (300 if args.command == "login" else 0)):
        return await execute(args)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # Third-party exception/log messages may include OAuth URLs or credential material.
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=True, indent=None if args.json else 2))
        if result.get("state") in {"FAILED", "EXPIRED", "ATTENTION_REQUIRED"}:
            return 1
        return 0
    except KeyboardInterrupt:
        error = CLIError(
            "interrupted", "Cancelled locally; queued operations are not cancelled.", 130
        )
    except Exception as exc:
        error = public_error(exc)
    finally:
        logging.disable(previous_logging)
    print(
        json.dumps({"error": {"code": error.code, "message": str(error)}}, ensure_ascii=True),
        file=sys.stderr,
    )
    return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
