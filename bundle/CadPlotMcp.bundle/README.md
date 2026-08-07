# CadPlotMcp.bundle

The build script copies each adapter DLL together with `CadPlotMcp.Core.dll` into its matching
`Contents/Windows/<release>` folder.
The package loader selects the 2016 adapter for AutoCAD R20.1 and the 2025 adapter for R25.0 through R25.1.

The plug-in opens the current-user-only local `cadplot-mcp` named pipe. Its size-limited,
newline-delimited JSON boundary has a positive command whitelist:

- `status`;
- `preview_publish_plan`;
- `validate_staged_job`;
- `queue_publish_job` (only when publishing was explicitly enabled before AutoCAD startup);
- `publish_job_status`.

All other commands return `command_not_allowed`. Queue requests cannot provide arbitrary commands,
workspace roots, output paths, page setups, or plot resources outside the independently validated
staged manifest. The executor opens only the staged DWG copy and closes it without saving.
