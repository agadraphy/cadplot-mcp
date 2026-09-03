"""MCP-independent CadPlot gateway/worker wire protocol."""

from cadplot_protocol.remote_protocol import PROTOCOL_VERSION
from cadplot_protocol.worker_http_protocol import HTTP_PROTOCOL_VERSION

__all__ = ["HTTP_PROTOCOL_VERSION", "PROTOCOL_VERSION"]
