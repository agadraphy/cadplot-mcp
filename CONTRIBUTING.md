# Contributing

Thanks for improving `cadplot-mcp`. Please open an issue before large changes
so the safety model and interface can be discussed.

## Development

Use Python 3.11 or newer, create an isolated environment, and install the
project's development dependencies. Before submitting a pull request, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/run-local-preflight.ps1 -AuditDependencies
```

This is the same no-AutoCAD-launch gate used by CI; it includes lint/tests, real MCP transport and
isolated-wheel smoke checks, the 300-drawing synthetic rehearsal, demo/release-kit integrity
smokes, .NET protocol builds/tests, exact locked dependency vulnerability checks, and a transitive
Python license inventory. The vulnerability result is current only at scan time and needs network
access to the configured advisory sources.

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
