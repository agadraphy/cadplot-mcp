# CadPlotMcp.bundle

The build script copies each adapter DLL together with `CadPlotMcp.Core.dll` into its matching
`Contents/Windows/<release>` folder.
The package loader selects the 2016 adapter only for AutoCAD R20.1 and the 2025 adapter only for
R25.0. AutoCAD 2026/R25.1 is deliberately outside this bundle's verified support boundary.
Each matching-SDK build is emitted into a new commit-bound release directory and accompanied by a
no-overwrite `bundle-build.json`. `verify-bundle-release.ps1` binds that manifest to the extracted
bundle and ZIP hashes; this is build evidence, not licensed plotting evidence.

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
