# CadPlot MCP Codex plugin

This optional repo-local plugin manifest starts the already installed `cadplot-mcp` CLI. It does
not bundle Python, AutoCAD, Autodesk assemblies, company plot resources, or a configuration file.

Before installing the plugin:

1. install CadPlot MCP into an isolated Python environment and expose `cadplot-mcp` on `PATH`;
2. set `CADPLOT_CONFIG` to the approved local configuration before starting Codex;
3. install the version-matched AutoCAD bundle separately;
4. keep `CADPLOT_ENABLE_PUBLISH` unset until the licensed one-sheet write pilot.

The wrapper inherits `CADPLOT_PIPE_NAME`. Leave it unset for the default `cadplot-mcp` bridge. If
licensed 2016 and 2025 processes run simultaneously, set it to the exact safe pipe name configured
for the selected AutoCAD process. Also set `CADPLOT_AUTOCAD_PROGID` to the exact version-specific
`AutoCAD.Application.20.1` or `AutoCAD.Application.25.0` identity and verify the reported COM and
plug-in runtime identities before staging.

The plugin declares write capability because staging creates verified copies and enabled publish
jobs create PDFs. Source DWGs remain outside the plugin and are never packaged with it.

This wrapper is for local Codex clients that can launch an MCP server over `stdio`. It is not a
ChatGPT web connector and it does not create a public HTTPS endpoint. A managed ChatGPT workspace
requires the separately governed bridge described in the repository's
[deployment modes](../../../docs/deployment-modes.md).
