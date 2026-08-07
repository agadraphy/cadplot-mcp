# CadPlotMcp.bundle

Copy the built adapter DLL together with `CadPlotMcp.Core.dll` into `Contents/Windows`.
The package loader selects the 2016 adapter for AutoCAD R20.1 and the 2025 adapter for R25.0 through R25.1.

The plug-in opens the local `cadplot-mcp` named pipe. Its newline-delimited JSON boundary intentionally accepts only:

```json
{"id":"optional-id","version":"1","command":"status"}
```

All other commands return `command_not_allowed`; no drawing operation is exposed through this pipe.
