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
- [ ] `cadplot-mcp.cdx.json` validates as the strict CycloneDX 1.7 profile, matches the exact commit,
      package, lock, runtime dependency count, and every kit artifact SHA-256, and contains no local
      paths, Autodesk binaries, company assets, or upgraded live-publish claims.
- [ ] `uv run ruff check .` passes.
- [ ] `uv run pytest` passes.
- [ ] `uv run python scripts/smoke-mcp-stdio.py` passes real subprocess initialize/list-tools and
      verifies the exact tool/annotation/instruction contract.
- [ ] `uv run python scripts/smoke-mcp-http.py` passes real loopback Streamable HTTP
      initialize/list-tools/tool calls and rejects hostile Host and Origin headers without launching
      AutoCAD; requests above 1 MiB receive `413`.
- [ ] `uv run python scripts/run-synthetic-demo.py` reports `source_unchanged=true` and
      `audit_complete=true`, while truthfully retaining `publish_verified=false`.
- [ ] `uv run python scripts/run-synthetic-batch-demo.py --drawings 300` reports 15 plan pages,
      15 staging batches, 300 unique jobs, unchanged sources, 300 complete structural PDF audits,
      no receipts, `execution_verified=0`, `publish_verified=0`, and 300 `manual_review` jobs.
- [ ] `uv build` and `uv run python scripts/audit-release-artifacts.py dist` pass.
- [ ] `uv run python scripts/smoke-wheel-install.py dist` installs the exact wheel into an isolated
      temporary environment using frozen, hash-checked lock dependencies and passes the real MCP
      STDIO/tool contract, loopback HTTP/header-guard contract, and isolated-inspector module
      protocol without source-tree import.
- [ ] The isolated wheel smoke records `tunnel_preflight_target_probed=true`: the installed package
      passed a secret/path-redacted local `initialize/list_tools` probe with the exact 20-tool
      surface while `autocad_launched=false` and `live_tunnel_proven=false` stayed explicit.
- [ ] `scripts/smoke-demo-kit.ps1` proves exact-tree/hash verification, wheel tamper rejection,
      removal of machine-local API paths, outer identity/archive hash rejection, and ZIP traversal
      rejection for the portable demo delivery.
- [ ] `dotnet build src/dotnet/CadPlotMcp.sln --configuration Release` passes.
- [ ] `dotnet test src/dotnet/CadPlotMcp.Core.Tests/CadPlotMcp.Core.Tests.csproj --configuration Release` passes.
- [ ] `scripts/probe-durable-queue.ps1` reports all fifteen exact production-core scenarios passed:
      pending recovery with identical request, no interrupted replay, terminal receipt recovery,
      completed-job requeue rejection, unsigned/altered/foreign-key/started-marker rejection, and a
      DPAPI-protected key outside the workspace, including corrupt-key rejection, plus exact
      idempotent pending cancellation, restart non-replay, authenticated cancel-marker enforcement,
      and running-job refusal. It also loads the built `net45` core under Windows
      .NET Framework and proves DPAPI key creation there; AutoCAD/live publish remain false.
- [ ] The compile-only API probe passes against an installed managed API folder, is labelled
      compile-only evidence, and records the correct release target (`R20.1/net45` or
      `R25.0/net8.0-windows`).
- [ ] The real bundle was built with Autodesk references while no Autodesk DLL was packaged.
- [ ] `check-autocad-api-series.ps1` reports one consistent `R20.1` set for the 2016 build and one
      consistent `R25.0` set for the 2025 build; directory names alone were not accepted.
- [ ] `scripts/verify-bundle.ps1` passes on the extracted bundle and its printed hashes are retained.
- [ ] The matching-SDK build used a clean commit, created a new no-overwrite release root, and
      `verify-bundle-release.ps1` matched `bundle-build.json`, ZIP entries, and all file hashes.
- [ ] Pilot bundle evidence parsed and hashed the same bounded stable `bundle-build.json` bytes,
      fingerprinted the ZIP before and after entry inspection, and rejected redirected, in-flight,
      or same-size/mtime-restored manifest/archive mutation without loading the ZIP into memory.
- [ ] The pilot assembler consumed direct bounded snapshots of the 2016/2025 run and recovery JSONs,
      rechecked all four after bundle/schema validation, and wrote the combined evidence only after
      every input remained exact and unchanged.
- [ ] `build-release-kit.ps1` bound the verified matching-SDK bundle, readiness-bound Python wheel,
      300-drawing rehearsal digest, durable-queue recovery evidence, dependency/license evidence,
      lock data, source archive, install scripts, and runbooks to the same clean commit.
- [ ] `verify-release-kit.ps1` matched both manifests, the exact kit tree, embedded bundle evidence,
      SBOM component/artifact hashes, and every outer ZIP entry without extraction; its live/public
      readiness flags remained false.
- [ ] Final acceptance used stable outer/inner manifest and pilot snapshots, pre/post release-ZIP
      fingerprints, retained kit-file fingerprints, and a final exact-tree comparison; mutation,
      redirect, or late additions failed before any live/public readiness decision.
- [ ] If production policy requires Authenticode, an authorized organization certificate and
      timestamp service signed the final DLLs and the signed hashes were re-piloted. Unsigned local
      builds are never described as signed.
- [ ] `install-release-kit.ps1 -WhatIf` previewed three non-overlapping destinations; the real run
      created/reused pilot then Python and made the exact verified AutoCAD bundle visible last.
- [ ] AutoCAD was closed and bundle mutation guards observed no `acad.exe`; the no-overwrite local
      install receipt matched release manifest, Python manifest, bundle hashes, paths, and false live flags.
- [ ] Standalone bundle install accepted only the exact release-root child after independently
      reverifying the sibling ZIP, `bundle-build.json`, exact commit, and matching-SDK evidence;
      no protocol-only fixture allowance was used on the workstation.
- [ ] `verify-release-install.ps1` independently matched that receipt to the transferred kit and
      installed targets; any expected config edit appeared only as `ConfigChangedSinceInstall=true`.
- [ ] With publish unset, `test-licensed-workstation.ps1` passed separately for 2016 and 2025 using
      exact versioned ProgIDs/pipes and matched live COM/runtime/adapter/commit/DLL hashes to the
      verified install; both no-overwrite records retained false live-publish/pilot-readiness claims.
- [ ] Each successful read-only verifier produced a separate no-overwrite `.mcp.json` whose single
      `mcpServers` entry used the verified installed Python/config/workspace/ProgID/pipe and omitted
      `CADPLOT_ENABLE_PUBLISH`.
- [ ] After each explicitly approved publish-enabled restart, the same verifier passed with
      `-SessionMode Publish` and the exact prior read-only record; doctor required authenticated
      queue state and identical receipt/config/runtime/binary identity before any job was queued.
- [ ] Only each successful publish-session `.mcp.json` was merged into the approved client; it used
      the matching release identity and exact `CADPLOT_ENABLE_PUBLISH=1`, without replacing unrelated
      client configuration.
- [ ] `install-python.ps1 -WhatIf` made no changes; the real install used the frozen lock with
      mandatory hashes, staged without overwrite, atomically renamed, and
      `verify-python-install.ps1` matched wheel/lock/requirements hashes and distribution inventory.
- [ ] `uninstall-python.ps1 -WhatIf` preserved the install; the real removal reverified it, atomically
      quarantined only the exact version/commit directory, and left no target or quarantine behind.
- [ ] `uninstall-bundle.ps1 -WhatIf` preserved `CadPlotMcp.bundle`; the real removal reverified every
      hash, atomically changed it to a non-`.bundle` quarantine, and left no quarantine behind.
- [ ] `uninstall-release-kit.ps1 -WhatIf` preserved both components; the real/resumed run removed
      bundle before Python and preserved the pilot workspace plus byte-identical install receipt.
- [ ] Install and uninstall `-WhatIf` targets were reviewed with AutoCAD closed; no overwrite path
      was introduced.
- [ ] `scripts/smoke-bundle-install.ps1` passes its protocol-only transactional copy/hash/install/
      uninstall test and is not represented as matching-SDK or live AutoCAD evidence.
- [ ] The portable demo delivery passes embedded `verify-demo-archive.ps1` before extraction and
      `verify-demo-kit.ps1` after extraction; its commit/hash is also compared through a trusted
      handoff channel because self-verification alone is not provenance.
- [ ] Package version and `CHANGELOG.md` are updated.
- [ ] README and API examples match the released behavior, where changed.
- [ ] Success, bounded-failure, and post-receipt PDF-tamper tests pass; output completeness is not
      presented as execution proof unless the schema-v2 receipt's canonical output-set SHA-256
      revalidates and `publish_verified=true`.
- [ ] Correct-size blank and non-painting content-stream PDFs are rejected; the 300-drawing
      rehearsal reports `blank_pdf_rejected=true` and `marking_content_verified=300`. Treat this as
      a blank-page guard, not as proof of crop, scale, lineweight, style, font, or title-block quality.
- [ ] Output audit rejects a redirected PDF/job path, files above the 128 MiB per-output limit, and
      files changed while their snapshot is read. Geometry, marking evidence, byte size, and SHA-256
      for every valid PDF come from the identical immutable byte snapshot.
- [ ] Manifest/receipt mutation tests prove that final auditing uses one stable manifest byte
      snapshot for paths, receipt digest, outputs, and source, rejects in-flight manifest/receipt
      changes, and re-hashes the manifest before returning evidence.
- [ ] Operations reporting does not reopen an audited manifest, exposes the exact snapshot digest on
      every valid job row, and rejects same-size/mtime-restored mutation. One-sheet and batch-recovery
      collectors consume and finally recheck that snapshot; recovery also matches the operations-row
      digest to its independent re-audit.
- [ ] Decoded page content above 64 MiB or pypdf's stricter decoder/aggregate limits is rejected as
      `pdf_content_limit_exceeded`; complete marking evidence must appear within an 8 MiB scan.
      Tokens inside strings, names, hexadecimal operands, or comments do not satisfy the nonblank
      gate, unbalanced operands are invalid structure, and no full pypdf operation list is materialized.
- [ ] Source-revision and timestamp-drift tests prove that staged validation, direct queueing,
      restart reporting, and final output audit all fail closed before claiming current output;
      `source_changed` jobs expose no requeue approval and `publish_verified=false`.
- [ ] Source/staged DWGs and source/staged DWG/DWT templates require two identical streaming
      fingerprints, reject redirected leaves and same-size/mtime-restored content change, and never
      load an unbounded drawing into memory. Cancellation markers use bounded stable byte snapshots
      and are re-hashed after structural validation.
- [ ] If an external DWG/DWT layout is used, its authorized local root, reviewed SHA-256, exact
      layout/page-setup names, single floating viewport, job-local copy hash, and manifest reference
      all match; the company asset remains outside Git and release artifacts.
- [ ] Each schema-v8 licensed pilot run contains the bound read-only/publish workstation records,
      their exact evidence-file SHA-256 values, the exact authenticated queue scheme, and a
      path-redacted `template_assets` list; every used
      asset's approved, post-pilot source, and staged-copy SHA-256 values are identical, or the list
      is explicitly empty when that run used no external template.
- [ ] Each run binds the exact nonblank published and authorized one-page reference PDFs by
      path-redacted SHA-256, byte length, and page geometry; the receipt output count/digest independently
      revalidates against the published PDF, their orientation/size relationship revalidates, and
      all seven visual checks were separately attested by the named reviewer. Reference geometry and
      SHA-256 come from one stable snapshot; in-flight change or decoded-content overflow is rejected.
- [ ] Operations-report cursor tests prove that restart pages do not repeat or skip staged jobs.
- [ ] `cadplot-collect-recovery` produced distinct 2–20 job, exact-workspace post-restart records
      for both AutoCAD 2016 and 2025; every retained job is receipt-output-bound,
      `publish_verified=true`, and source/staged-hash unchanged.
- [ ] Dependency lock data has been reviewed for intended versions.

## Alpha release criteria

- [ ] The change has focused test coverage and a documented safety impact.
- [ ] At least one dry-run transcript or equivalent manual result is reviewed.
- [ ] `cadplot-tunnel-preflight --probe-target --output <new-file>` produced a secret-free exact
      20-tool report for the installed release.
- [ ] `cadplot-chatgpt-eval prepare` produced the canonical 13-case plan bound to that report's
      `tool_surface_sha256` without overwriting existing evidence.
- [ ] An authorized workspace evaluator completed all direct, indirect, follow-up, approval,
      adversarial, edge, cancellation, and recovery cases; the sanitized validator returned
      `valid=true`, `passed_case_count=13`, and kept live publish/licensed AutoCAD claims false.
- [ ] Synthetic demo evidence is labelled synthetic and is not presented as AutoCAD evidence.
- [ ] Known limitations and incompatible changes are stated in release notes.
- [ ] A maintainer has reviewed the release artifacts before publication.
- [ ] Licensed AutoCAD 2016 and 2025 live results are recorded separately; one version's result is
      not treated as proof for the other.
- [ ] External-template import, when used by the office profile, is visually accepted separately on
      both licensed versions; compile-only `ReadDwgFile`/`WblockCloneObjects` coverage is not live proof.
- [ ] `cadplot-validate-pilot` returns `valid=true` for the locally retained two-version
      acceptance record.
- [ ] `cadplot-collect-pilot` produced each run from the live plug-in and immutable job evidence;
      live `buildCommit`/`pluginSha256` matched the corresponding adapter entry, and
      `cadplot-assemble-pilot` bound both runs plus both `cadplot-collect-recovery` records to the
      exact verified bundle/build manifest.
- [ ] `cadplot-acceptance finalize` bound the full transferred release kit and schema-v8
      two-version one-sheet/recovery evidence into a new sanitized schema-v2 report; `validate`
      replays the same hashes successfully.
- [ ] `public_release_ready=true` appears only when both company-publication and maintainer-release
      approvals were explicitly supplied; the full company pilot evidence remains outside Git.
- [ ] The demo operator reviewed `docs/pazartesi-demo-tr.md` and can state the title-block and
      managed-ChatGPT boundaries without overstating readiness.

## Publish

- [ ] Build in a clean environment.
- [ ] Inspect source and wheel contents before upload.
- [ ] Tag and publish only after the checks above are complete.
- [ ] Do not publish the combined kit until separate licensed 2016/2025 pilot evidence is reviewed.
