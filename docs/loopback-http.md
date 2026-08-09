# Loopback Streamable HTTP transport

CadPlot ships a second MCP entry point for an authorized local tunnel:

```powershell
$env:CADPLOT_CONFIG = "C:\CadPlotPilot\config.yaml"
cadplot-mcp-http --port 8765
```

The endpoint is `http://127.0.0.1:8765/mcp`. It exposes the same 19 typed tools and approval
contract as `cadplot-mcp` over STDIO. It does not launch AutoCAD.

## Fixed security boundary

The command intentionally:

- binds only IPv4 loopback (`127.0.0.1`); there is no `--host` option;
- accepts only the exact `127.0.0.1:<port>` or `localhost:<port>` Host headers;
- rejects every request carrying an Origin header, so it is not a browser API;
- caps request bodies at 1 MiB;
- accepts only an unprivileged explicit port from 1024 through 65535;
- keeps the same allowed-root, copy-only, plan-ID, manifest-digest, and publish opt-in gates.

Run `python scripts/smoke-mcp-http.py` to start a real temporary server, perform MCP
initialize/list-tools/structured tool calls, and prove that hostile Host and Origin headers are
rejected and oversized requests receive `413`. The smoke uses temporary non-DWG folders and never
launches AutoCAD.

## ChatGPT web boundary

ChatGPT web connects to remote MCP servers, not directly to a workstation-local port. For a
developer pilot, an authorized workspace administrator can provision OpenAI Secure MCP Tunnel and
target this loopback endpoint. The official client can also launch `cadplot-mcp` directly over
STDIO, which is the recommended single-workstation handoff because no listening MCP socket is
needed. Run `cadplot-tunnel-preflight --transport stdio --probe-target` first; see
[Secure MCP Tunnel handoff](secure-tunnel-handoff.md).
The tunnel is responsible for the remote encrypted/authenticated edge; this local process
deliberately does not implement OAuth or expose a public listener.

Do not forward this port with a generic reverse proxy, router rule, public tunnel, or TCP relay.
Do not change the bind address in a local fork and call it production-ready. A managed company
deployment still requires a separately reviewed authenticated HTTPS gateway, organization/device
authorization, audit retention, revocation, and workstation routing.

The tunnel/app scan and admin publication are external live gates. They do not prove AutoCAD 2016
or 2025 plotting; each licensed version still needs its own retained pilot evidence.

## References

- [OpenAI developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt)
- [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
