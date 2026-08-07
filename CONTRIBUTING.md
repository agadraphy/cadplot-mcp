# Contributing

Thanks for improving `cadplot-mcp`. Please open an issue before large changes
so the safety model and interface can be discussed.

## Development

Use Python 3.11 or newer, create an isolated environment, and install the
project's development dependencies. Before submitting a pull request, run:

```powershell
uv run ruff check .
uv run pytest
dotnet build src/dotnet/CadPlotMcp.sln --configuration Release
dotnet test src/dotnet/CadPlotMcp.Core.Tests/CadPlotMcp.Core.Tests.csproj --configuration Release
```

Keep changes focused, document user-visible behavior, and add or update tests
for changed safety rules.

## CAD safety requirements

Contributions must preserve these invariants:

- Inspect and plan by default; make execution explicit.
- Use dry-run before any action that could affect a drawing or plot output.
- Never overwrite, move, delete, or modify source drawings implicitly.
- Prefer copy-only output paths and require clear operator confirmation for
  actions outside them.
- Do not add telemetry, network upload, or hidden AutoCAD automation.

Use synthetic or permissioned fixtures only. Do not commit client drawings,
license files, credentials, or proprietary assets.

## Pull requests

Describe the motivation, safety impact, test evidence, and any AutoCAD version
used for manual verification. Maintainers may request a dry-run transcript or
an updated release note before merging.
