# ChatGPT connection architecture

## What the user can say

A user can ask a connected client to inspect a configured project folder, create dry-run plans,
stage exact approved plans, queue exact approved manifests, and audit the resulting PDFs. The model
chooses typed CadPlot tools; it does not generate AutoLISP, shell commands, or arbitrary AutoCAD
commands.

"Upload the DWG to ChatGPT" is not the normal architecture. The DWG stays inside an authorized
local source root. A local client passes its path to the local MCP server. For ChatGPT web, a
managed bridge must route an authorized job reference to a registered workstation without exposing
the workstation filesystem as a general remote service.

```mermaid
flowchart LR
    U["Authorized user"] --> C["Codex or ChatGPT client"]
    C -->|"typed MCP tools"| P["CadPlot Python server"]
    P -->|"read-only COM inspection"| A["Licensed AutoCAD"]
    P -->|"approved plan and manifest only"| N["Current-user named pipe"]
    N --> A
    P --> W["Isolated job workspace"]
    A --> W
    W --> E["PDF and immutable receipt evidence"]
```

For a local client, `C -> P` is `stdio` on the workstation. For ChatGPT web, only that edge changes:
it becomes a company-authorized Secure MCP Tunnel during development or an authenticated HTTPS
Streamable HTTP `/mcp` gateway in production. Everything from `P` onward remains workstation-local.

## Required operation sequence

1. Validate configuration, allowed roots, workspace separation, and the AutoCAD connection.
2. Inspect the exact DWG and create a deterministic dry-run plan.
3. Resolve every blocker and optionally preview the ready plan with the plug-in.
4. Obtain explicit approval for the exact `plan_id`; then stage an isolated copy.
5. Validate the staged manifest with the plug-in.
6. Obtain explicit approval for the exact `plan_id` and `manifest_sha256`; then queue publishing.
7. Read terminal receipt evidence and audit every expected PDF.
8. Report completion only when `publish_verified=true`.

The MCP server advertises this sequence in its server instructions and marks local write tools with
write annotations. Client approvals remain mandatory because annotations are descriptive hints,
not authorization.

## Implemented and unimplemented boundaries

Implemented in this repository:

- local `stdio` MCP server;
- optional validated Codex plugin wrapper;
- local Python/AutoCAD named-pipe protocol;
- approval-bound staging and publishing;
- immutable receipt and PDF audit evidence.

Not implemented or claimed:

- a hosted Streamable HTTP gateway;
- OAuth, mutual TLS, device enrollment, or organization user mapping;
- Secure MCP Tunnel provisioning;
- public ChatGPT plugin submission;
- live AutoCAD 2016 and 2025 acceptance evidence.

Those are separate delivery gates. A successful local synthetic test or compile probe must never be
presented as proof of any of them.
