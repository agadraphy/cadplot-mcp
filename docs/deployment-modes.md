# Deployment modes

## Local workstation pilot

Recommended first deployment:

1. A local MCP client launches `cadplot-mcp` over `stdio`.
2. The Python process reads only configured project/workspace paths.
3. The installed AutoCAD bundle exposes a local named pipe.
4. AutoCAD remains user-visible and runs on the same licensed Windows workstation.

The installed plug-in is read-only by default. `CADPLOT_ENABLE_PUBLISH=1` must be set before
AutoCAD starts to expose the approval-gated queue. Keep this off until staging validation passes.

No inbound network service is required. The named pipe must never be exposed through a public
port or generic command relay.

## Managed company ChatGPT

A centrally managed ChatGPT workspace cannot implicitly access a user's local AutoCAD process.
The organization must approve its MCP connector and provide a managed secure bridge/tunnel to the
workstation-side service. Authentication, device identity, authorization, audit retention, and
connector ownership are IT decisions, not defaults inferred by CadPlot MCP.

The remote side should carry approved plan/job messages only. AutoCAD COM and the local named pipe
remain workstation-local. Proprietary DWGs and plot resources should remain local unless company
policy explicitly authorizes transfer.

OpenAI's current guidance for managed ChatGPT MCP apps, developer mode, write actions, and the
Secure MCP Tunnel is documented at:

<https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt>

## Public repository

The public repository contains code, examples, schemas, and documentation only. Never publish:

- client/company DWGs or PDFs;
- DWT/title-block libraries;
- PC3, PMP, CTB, or STB files;
- Autodesk SDK/product assemblies;
- connector credentials, tokens, internal hostnames, or allowed-root paths.
