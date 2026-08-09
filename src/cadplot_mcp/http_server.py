from __future__ import annotations

import argparse
from collections.abc import Sequence

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from cadplot_mcp.server import mcp

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MIN_PORT = 1024
MAX_PORT = 65535
MAX_REQUEST_BODY_BYTES = 1024 * 1024
MCP_PATH = "/mcp"


def _port(value: str | int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if port < MIN_PORT or port > MAX_PORT:
        raise argparse.ArgumentTypeError(
            f"port must be between {MIN_PORT} and {MAX_PORT}"
        )
    return port


def configure_loopback_transport(server: FastMCP, *, port: int) -> FastMCP:
    """Configure a FastMCP instance as a loopback-only Streamable HTTP endpoint."""
    validated_port = _port(port)
    server.settings.host = LOOPBACK_HOST
    server.settings.port = validated_port
    server.settings.streamable_http_path = MCP_PATH
    server.settings.max_request_body_size = MAX_REQUEST_BODY_BYTES
    server.settings.json_response = False
    server.settings.stateless_http = False
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            f"{LOOPBACK_HOST}:{validated_port}",
            f"localhost:{validated_port}",
        ],
        # Native/tunnel MCP clients omit Origin. Reject every browser-originated
        # request instead of turning this endpoint into a local web API.
        allowed_origins=[],
    )
    return server


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run CadPlot MCP on a loopback-only Streamable HTTP endpoint for an "
            "authorized local MCP tunnel."
        )
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        help=f"loopback TCP port ({MIN_PORT}-{MAX_PORT}; default: {DEFAULT_PORT})",
    )
    args = parser.parse_args(argv)
    configure_loopback_transport(mcp, port=args.port)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
