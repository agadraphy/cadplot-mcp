# Deployment modes

CadPlot MCP has two boundaries that must not be confused:

- the MCP client-to-Python connection; and
- the workstation-local Python-to-AutoCAD connection.

The AutoCAD named pipe, COM automation, authorized DWGs, and company plot resources always stay
on the licensed Windows workstation. A remote MCP endpoint is a controlled message bridge, not a
public AutoCAD port or file share.

| Mode | MCP connection | AutoCAD worker | Intended use | Current status |
| --- | --- | --- | --- | --- |
| Local workstation | `stdio` launched by Codex or ChatGPT desktop | Same Windows workstation | First licensed pilot and normal single-user operation | Implemented |
| ChatGPT web developer pilot | Secure MCP Tunnel to local STDIO (recommended) or loopback HTTP | Same Windows workstation | Temporary Business/Enterprise/Edu evaluation | Local targets/preflight implemented; tunnel/admin provisioning external |
| Managed company deployment | Authenticated, publicly reachable HTTPS Streamable HTTP `/mcp` proxy | Registered company workstations | Centrally governed internal use | Architecture only |
| Public ChatGPT plugin | Stable public HTTPS Streamable HTTP `/mcp`, verified domain, review requirements | Requires a separately designed managed worker service | Marketplace/public distribution | Not implemented or claimed |

## Local workstation pilot

Recommended first deployment:

1. A local MCP client launches `cadplot-mcp` over `stdio`.
2. The Python process reads only configured project/workspace paths.
3. The installed AutoCAD bundle exposes a current-user local named pipe.
4. AutoCAD remains user-visible and runs on the same licensed Windows workstation.

The installed plug-in is read-only by default. `CADPLOT_ENABLE_PUBLISH=1` must be set before
AutoCAD starts to expose the approval-gated queue. Keep this off until staging validation passes.

The default current-user pipe name is `cadplot-mcp`, which intentionally supports one selected
AutoCAD bridge per MCP process. If licensed 2016 and 2025 processes must be open simultaneously,
launch each AutoCAD process and its corresponding MCP server with a different matching
`CADPLOT_PIPE_NAME` such as `cadplot-mcp-2016` and `cadplot-mcp-2025`. Both implementations accept
only `[A-Za-z0-9._-]{1,128}` and fail closed on any other value. Set the matching MCP process's
`CADPLOT_AUTOCAD_PROGID` to `AutoCAD.Application.20.1` for 2016 or
`AutoCAD.Application.25.0` for 2025 so read-only inspection selects the same release. CadPlot checks
the returned ActiveX application version and rejects ambiguous or foreign ProgIDs. Record the
selected process identity from `validate_environment` and `get_autocad_plugin_status` before
inspecting or queueing work. In full doctor mode an exact ProgID and the plug-in's `runtimeSeries`
must agree; `inspection_identity_matched=false` blocks readiness.
The installed `test-licensed-workstation.ps1` command first records this identity with publishing
off. After an approved restart it can be run with `-SessionMode Publish` and that prior record;
`cadplot-doctor --expect-publish-enabled` then requires the same release/binary identity plus the
authenticated queue before any job is accepted. Neither check opens or queues a drawing.
Each successful check creates a separate no-overwrite standard `mcpServers` config bound to the
verified installed interpreter and session environment. Read-only output omits the publish flag;
publish output contains exact `CADPLOT_ENABLE_PUBLISH=1` only after the authenticated session gate
succeeds.
The plug-in reserves its single current-user pipe instance before initialization returns; a second
AutoCAD process configured with the same name fails visibly instead of becoming an ambiguous hidden
listener. The same server instance stays reserved across sequential MCP connections.

No inbound network service is required. The named pipe must never be exposed through a public
port or generic command relay.

## ChatGPT web developer pilot

ChatGPT web cannot read a workstation's local MCP configuration, local file paths, or AutoCAD
process by implication. An authorized workspace administrator must enable the required plugin or
connector controls. A developer can then connect ChatGPT to either:

- a public HTTPS Streamable HTTP MCP endpoint; or
- OpenAI's Secure MCP Tunnel for a temporary developer-mode bridge.

The tunnel is a development aid, not public deployment evidence and not a substitute for the two
licensed AutoCAD acceptance runs. Only plan/job messages should cross the bridge. Do not upload or
copy proprietary DWGs, PC3/PMP, CTB/STB, DWT, or title-block assets unless company policy explicitly
authorizes it.

OpenAI's tunnel client can launch installed `cadplot-mcp` over STDIO; this is the recommended
single-workstation mode because it adds no listening MCP socket. `cadplot-tunnel-preflight
--probe-target` checks the local configuration and performs a real MCP initialize/tool-surface
probe while reporting tunnel IDs and runtime keys only as booleans. It never calls a CadPlot tool,
starts a tunnel, prints a secret, checks admin permissions, or launches AutoCAD. See
[Secure MCP Tunnel handoff](secure-tunnel-handoff.md).

The installed `cadplot-chatgpt-eval` command turns that probed surface fingerprint into a canonical
13-case tool-selection plan and validates a sanitized external result record. See
[ChatGPT tool-selection evaluation](chatgpt-evaluation.md). This proves ChatGPT selection and
confirmation behavior, not AutoCAD output.

CadPlot also ships `cadplot-mcp-http --port 8765` as a loopback-only target. It binds only
`127.0.0.1`, enforces exact Host headers, rejects browser Origin headers, and caps requests at 1 MiB.
It has no public listener or application-level OAuth and must not be placed behind a generic public
reverse proxy. See [loopback Streamable HTTP transport](loopback-http.md).

## Managed company deployment

A production bridge must terminate authenticated HTTPS and forward only the explicit CadPlot MCP
schemas. OpenAI's current enterprise guidance supports managed authorization patterns including
OAuth and OpenAI-managed mutual TLS, subject to workspace policy. The organization owns device
identity, user authorization, audit retention, revocation, availability, and workstation routing.

The bridge must not:

- expose AutoCAD COM, the local named pipe, or an arbitrary command endpoint;
- accept unrestricted filesystem paths or a workspace root from remote requests;
- bypass the exact `plan_id` and `manifest_sha256` approval gates;
- claim success before final `source_unchanged=true` and `publish_verified=true` evidence exists.

This repository does not yet implement that managed HTTPS proxy. The local `stdio` wrapper and
loopback HTTP endpoint must not be represented as a production ChatGPT web connector.

## Public ChatGPT plugin

Public submission requires a stable publicly reachable HTTPS Streamable HTTP MCP server and the
applicable domain verification and review process. Secure MCP Tunnel endpoints are not eligible as
the production endpoint. CadPlot's workstation-bound AutoCAD executor therefore needs a separate,
security-reviewed gateway/worker architecture before public ChatGPT distribution can be claimed.

The public GitHub repository can still distribute the local MCP server and AutoCAD bundle source
under MIT without being a public ChatGPT plugin.

## Public repository boundary

Never publish:

- client/company DWGs or PDFs;
- DWT/title-block libraries;
- PC3, PMP, CTB, or STB files;
- Autodesk SDK/product assemblies;
- connector credentials, tokens, internal hostnames, or allowed-root paths.

## Current OpenAI references

- [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [Connect and test a plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [MCP for ChatGPT Enterprise](https://learn.chatgpt.com/docs/extend/mcp)
- [Enterprise apps and connectors](https://learn.chatgpt.com/docs/enterprise/apps-and-connectors)
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

See [ChatGPT connection architecture](chatgpt-connection.md) for the exact data path and acceptance
boundary.
