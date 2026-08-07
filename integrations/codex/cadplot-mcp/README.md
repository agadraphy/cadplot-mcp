# CadPlot MCP Codex plugin

This optional repo-local plugin manifest starts the already installed `cadplot-mcp` CLI. It does
not bundle Python, AutoCAD, Autodesk assemblies, company plot resources, or a configuration file.

Before installing the plugin:

1. install CadPlot MCP into an isolated Python environment and expose `cadplot-mcp` on `PATH`;
2. set `CADPLOT_CONFIG` to the approved local configuration before starting Codex;
3. install the version-matched AutoCAD bundle separately;
4. keep `CADPLOT_ENABLE_PUBLISH` unset until the licensed one-sheet write pilot.

The plugin declares write capability because staging creates verified copies and enabled publish
jobs create PDFs. Source DWGs remain outside the plugin and are never packaged with it.

This wrapper is for local Codex clients that can launch an MCP server over `stdio`. It is not a
ChatGPT web connector and it does not create a public HTTPS endpoint. A managed ChatGPT workspace
requires the separately governed bridge described in `docs/deployment-modes.md` at repository root.
