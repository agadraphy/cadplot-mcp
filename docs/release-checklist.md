# Release Checklist

Use this checklist for every alpha release.

## Safety and scope

- [ ] All drawing-affecting paths require explicit execution after a dry run.
- [ ] Source drawings are never overwritten, moved, or deleted implicitly.
- [ ] Output locations are explicit, writable, and separate from source inputs.
- [ ] Manual AutoCAD verification used a non-production copy of a drawing.
- [ ] No proprietary drawings, credentials, Autodesk binaries, or company assets
      are included in the source distribution or wheel.

## Quality

- [ ] `scripts/run-local-preflight.ps1` passes and truthfully reports
      `autocad_launched=false`, `live_publish_proven=false` before the licensed pilot.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run pytest` passes.
- [ ] `uv run python scripts/smoke-mcp-stdio.py` passes real subprocess initialize/list-tools and
      verifies the exact tool/annotation/instruction contract.
- [ ] `uv run python scripts/run-synthetic-demo.py` reports `source_unchanged=true` and
      `audit_complete=true`, while truthfully retaining `publish_verified=false`.
- [ ] `uv build` and `uv run python scripts/audit-release-artifacts.py dist` pass.
- [ ] `dotnet build src/dotnet/CadPlotMcp.sln --configuration Release` passes.
- [ ] `dotnet test src/dotnet/CadPlotMcp.Core.Tests/CadPlotMcp.Core.Tests.csproj --configuration Release` passes.
- [ ] The compile-only API probe passes against an installed managed API folder and is labelled
      compile-only evidence.
- [ ] The real bundle was built with Autodesk references while no Autodesk DLL was packaged.
- [ ] `scripts/verify-bundle.ps1` passes on the extracted bundle and its printed hashes are retained.
- [ ] Install and uninstall `-WhatIf` targets were reviewed with AutoCAD closed; no overwrite path
      was introduced.
- [ ] Package version and `CHANGELOG.md` are updated.
- [ ] README and API examples match the released behavior, where changed.
- [ ] Success and bounded-failure receipt tests pass; output completeness is not presented as
      execution proof unless `publish_verified=true`.
- [ ] Operations-report cursor tests prove that restart pages do not repeat or skip staged jobs.
- [ ] Dependency lock data has been reviewed for intended versions.

## Alpha release criteria

- [ ] The change has focused test coverage and a documented safety impact.
- [ ] At least one dry-run transcript or equivalent manual result is reviewed.
- [ ] Synthetic demo evidence is labelled synthetic and is not presented as AutoCAD evidence.
- [ ] Known limitations and incompatible changes are stated in release notes.
- [ ] A maintainer has reviewed the release artifacts before publication.
- [ ] Licensed AutoCAD 2016 and 2025 live results are recorded separately; one version's result is
      not treated as proof for the other.
- [ ] `scripts/validate-pilot-evidence.py` returns `valid=true` for the locally retained two-version
      acceptance record.
- [ ] `collect-pilot-run.py` produced each run from the live plug-in and immutable job evidence;
      `assemble-pilot-evidence.py` bound both runs to the exact verified bundle and commit.
- [ ] The demo operator reviewed `docs/pazartesi-demo-tr.md` and can state the title-block and
      managed-ChatGPT boundaries without overstating readiness.

## Publish

- [ ] Build in a clean environment.
- [ ] Inspect source and wheel contents before upload.
- [ ] Tag and publish only after the checks above are complete.
