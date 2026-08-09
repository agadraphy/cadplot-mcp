# Release Checklist

Use this checklist for every alpha release.

## Safety and scope

- [ ] All drawing-affecting paths require explicit execution after a dry run.
- [ ] Source drawings are never overwritten, moved, or deleted implicitly.
- [ ] Output locations are explicit, writable, and separate from source inputs.
- [ ] Manual AutoCAD verification used a non-production copy of a drawing.
- [ ] No proprietary drawings, credentials, Autodesk binaries, or company assets
      are included in the source distribution or wheel.
- [ ] `uv run python scripts/audit-source-tree.py` passes against tracked and non-ignored source
      files before commit or publication.

## Quality

- [ ] `scripts/run-local-preflight.ps1 -AuditDependencies` passes and truthfully reports
      `autocad_launched=false`, `live_publish_proven=false` before the licensed pilot.
- [ ] Dependency evidence matches the current `uv.lock`, covers every locked production Python
      package and all four .NET projects, reports zero known vulnerabilities and zero unknown Python
      license declarations, and records the scan timestamp. Treat it as point-in-time evidence.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run pytest` passes.
- [ ] `uv run python scripts/smoke-mcp-stdio.py` passes real subprocess initialize/list-tools and
      verifies the exact tool/annotation/instruction contract.
- [ ] `uv run python scripts/run-synthetic-demo.py` reports `source_unchanged=true` and
      `audit_complete=true`, while truthfully retaining `publish_verified=false`.
- [ ] `uv run python scripts/run-synthetic-batch-demo.py --drawings 300` reports 15 plan pages,
      15 staging batches, 300 unique jobs, unchanged sources, 300 complete structural PDF audits,
      no receipts, `execution_verified=0`, `publish_verified=0`, and 300 `manual_review` jobs.
- [ ] `uv build` and `uv run python scripts/audit-release-artifacts.py dist` pass.
- [ ] `uv run python scripts/smoke-wheel-install.py dist` installs the exact wheel into an isolated
      temporary environment using frozen, hash-checked lock dependencies and passes the real MCP
      STDIO/tool contract without source-tree import.
- [ ] `scripts/smoke-demo-kit.ps1` proves exact-tree/hash verification, wheel tamper rejection, and
      removal of machine-local API paths from the portable demo manifest.
- [ ] `dotnet build src/dotnet/CadPlotMcp.sln --configuration Release` passes.
- [ ] `dotnet test src/dotnet/CadPlotMcp.Core.Tests/CadPlotMcp.Core.Tests.csproj --configuration Release` passes.
- [ ] The compile-only API probe passes against an installed managed API folder, is labelled
      compile-only evidence, and records the correct release target (`R20.1/net45` or
      `R25.0/net8.0-windows`).
- [ ] The real bundle was built with Autodesk references while no Autodesk DLL was packaged.
- [ ] `check-autocad-api-series.ps1` reports one consistent `R20.1` set for the 2016 build and one
      consistent `R25.0` set for the 2025 build; directory names alone were not accepted.
- [ ] `scripts/verify-bundle.ps1` passes on the extracted bundle and its printed hashes are retained.
- [ ] The matching-SDK build used a clean commit, created a new no-overwrite release root, and
      `verify-bundle-release.ps1` matched `bundle-build.json`, ZIP entries, and all file hashes.
- [ ] `build-release-kit.ps1` bound the verified matching-SDK bundle, readiness-bound Python wheel,
      300-drawing rehearsal digest, dependency/license evidence, lock data, source archive, install
      scripts, and runbooks to the same clean commit.
- [ ] `verify-release-kit.ps1` matched both manifests, the exact kit tree, embedded bundle evidence,
      and every outer ZIP entry without extraction; its live/public readiness flags remained false.
- [ ] Install and uninstall `-WhatIf` targets were reviewed with AutoCAD closed; no overwrite path
      was introduced.
- [ ] `scripts/smoke-bundle-install.ps1` passes its protocol-only transactional copy/hash/install/
      uninstall test and is not represented as matching-SDK or live AutoCAD evidence.
- [ ] The portable demo folder passes its embedded `verify-demo-kit.ps1`; its commit/hash is also
      compared through a trusted handoff channel because self-verification alone is not provenance.
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
- [ ] `cadplot-validate-pilot` returns `valid=true` for the locally retained two-version
      acceptance record.
- [ ] `cadplot-collect-pilot` produced each run from the live plug-in and immutable job evidence;
      live `buildCommit`/`pluginSha256` matched the corresponding adapter entry, and
      `cadplot-assemble-pilot` bound both runs to the exact verified bundle/build manifest.
- [ ] The demo operator reviewed `docs/pazartesi-demo-tr.md` and can state the title-block and
      managed-ChatGPT boundaries without overstating readiness.

## Publish

- [ ] Build in a clean environment.
- [ ] Inspect source and wheel contents before upload.
- [ ] Tag and publish only after the checks above are complete.
- [ ] Do not publish the combined kit until separate licensed 2016/2025 pilot evidence is reviewed.
