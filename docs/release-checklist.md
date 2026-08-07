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

- [ ] `uv run ruff check .` passes.
- [ ] `uv run pytest` passes.
- [ ] `uv run python scripts/run-synthetic-demo.py` reports `source_unchanged=true` and
      `audit_complete=true`.
- [ ] `dotnet build src/dotnet/CadPlotMcp.sln --configuration Release` passes.
- [ ] `dotnet test src/dotnet/CadPlotMcp.Core.Tests/CadPlotMcp.Core.Tests.csproj --configuration Release` passes.
- [ ] The compile-only API probe passes against an installed managed API folder and is labelled
      compile-only evidence.
- [ ] The real bundle was built with Autodesk references while no Autodesk DLL was packaged.
- [ ] Package version and `CHANGELOG.md` are updated.
- [ ] README and API examples match the released behavior, where changed.
- [ ] Dependency lock data has been reviewed for intended versions.

## Alpha release criteria

- [ ] The change has focused test coverage and a documented safety impact.
- [ ] At least one dry-run transcript or equivalent manual result is reviewed.
- [ ] Synthetic demo evidence is labelled synthetic and is not presented as AutoCAD evidence.
- [ ] Known limitations and incompatible changes are stated in release notes.
- [ ] A maintainer has reviewed the release artifacts before publication.
- [ ] Licensed AutoCAD 2016 and 2025 live results are recorded separately; one version's result is
      not treated as proof for the other.

## Publish

- [ ] Build in a clean environment.
- [ ] Inspect source and wheel contents before upload.
- [ ] Tag and publish only after the checks above are complete.
