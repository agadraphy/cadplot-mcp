# Licensed pilot evidence

Live acceptance is complete only when one AutoCAD 2016 run and one AutoCAD 2025 run are recorded
in a local JSON file and pass:

```powershell
cadplot-validate-pilot C:\CadPlotPilot\pilot-evidence.json
```

Keep the completed evidence under the company's approved audit location; do not commit internal
names, paths, drawings, PDFs, or approvals to the public repository.

## Collect without manual hash copying

After the one-sheet job has succeeded, AutoCAD has been restarted, the persistent receipt has been
rechecked, and the authorized CAD reviewer has accepted all seven visual checks, collect each run:

```powershell
cadplot-collect-pilot C:\CadPlotPilot\2016-job\manifest.json `
  --release 2016 `
  --approved-by "Authorized CAD manager" `
  --output C:\CadPlotPilot\run-2016.json `
  --licensed `
  --authorized-test-asset `
  --restart-receipt-verified `
  --accept-visual-checks
```

Repeat with the 2025 job and `--release 2025`. The collector is read-only with respect to the job,
DWG, receipt, and PDFs. It queries the live named-pipe status, requires publishing/workspace to be
enabled, requires `runtimeSupported=true`, records the normalized `runtimeSeries`, the running
adapter's embedded `buildCommit`, and on-disk `pluginSha256`, audits
`publish_verified=true`, re-hashes source/staged files, requires exactly one PDF, and refuses to
overwrite an existing evidence file. The four declaration flags are human
attestations; do not pass them before the corresponding checks are actually complete.

First verify the matching-SDK release root, then assemble both distinct run files with its exact
bundle archive and `bundle-build.json`:

```powershell
.\scripts\verify-bundle-release.ps1 -ReleaseRoot C:\CadPlotPilot\bundle-release
cadplot-assemble-pilot `
  --run-2016 C:\CadPlotPilot\run-2016.json `
  --run-2025 C:\CadPlotPilot\run-2025.json `
  --bundle C:\CadPlotPilot\bundle-release\CadPlotMcp.bundle.zip `
  --bundle-build-manifest C:\CadPlotPilot\bundle-release\bundle-build.json `
  --output C:\CadPlotPilot\pilot-evidence.json

cadplot-validate-pilot C:\CadPlotPilot\pilot-evidence.json
```

These commands are installed by the same verified wheel included in the release kit. The source
checkout retains equivalent thin scripts for development and review. The assembler independently
re-hashes and inspects every bundle ZIP entry, validates the exact
`R20.1`/`R25.0` SDK identities and build flags, derives the commit instead of accepting operator
input, and requires each live `pluginSha256` to equal the corresponding adapter DLL hash in the
manifest. It revalidates both runs, requires distinct 2016/2025 evidence, and refuses to overwrite
its output.

The schema-v2 top level contains the full `repository_commit`, `package_version`, bundle and build
manifest SHA-256 values, exact 2016/2025 adapter hashes, and exactly two `runs`. Each run records:

- `autocad_release`, live `product` including normalized and raw ACADVER, exact `adapter`, normalized
  `runtime_series`, embedded `build_commit`, and running `plugin_sha256` identity;
- explicit `licensed=true` and `authorized_test_asset=true` declarations;
- approved `plan_id`, manifest digest, and matching receipt manifest digest;
- source and staged DWG SHA-256 values before and after plotting;
- produced PDF SHA-256, successful receipt state, `publish_verified=true`, and proof that the
  receipt remained readable after restart;
- seven explicit visual checks: orientation, crop, viewport scale, lineweights, plot style, fonts,
  and title block;
- the authorized approver and a timezone-qualified completion timestamp.

The validator rejects missing/extra fields, duplicate releases, incorrect adapter/runtime-series
pairs, incorrect ACADVER product identity,
running commit/binary mismatches, changed DWG hashes, a receipt bound to another manifest,
incomplete visual acceptance, or a run that was not rechecked after AutoCAD restart. A valid report
proves the recorded gates only; the actual evidence files and licensed workstation remain
authoritative.

After both runs validate, use the [release acceptance](release-acceptance.md) gate to bind this local
evidence to the exact transferred release kit and produce a sanitized no-overwrite readiness report.
