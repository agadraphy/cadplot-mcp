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
  --reference-pdf C:\CadPlotPilot\pilot-input\approved\reference-2016.pdf `
  --output C:\CadPlotPilot\run-2016.json `
  --licensed `
  --authorized-test-asset `
  --restart-receipt-verified `
  --accept-orientation `
  --accept-crop `
  --accept-viewport-scale `
  --accept-lineweights `
  --accept-plot-style `
  --accept-fonts `
  --accept-title-block
```

Repeat with the 2025 job and `--release 2025`. The collector is read-only with respect to the job,
DWG, receipt, and PDFs. It queries the live named-pipe status, requires publishing/workspace to be
enabled, requires `runtimeSupported=true`, records the normalized `runtimeSeries`, the running
adapter's embedded `buildCommit`, on-disk `pluginSha256`, and exact
`queueAuthentication=windows-dpapi-current-user+hmac-sha256-v1`, audits
`publish_verified=true`, re-hashes source/staged files, requires exactly one PDF, and refuses to
overwrite an existing evidence file. These declaration flags are human attestations; do not pass
them before the corresponding checks are actually complete. The seven
visual checks are deliberately separate flags. There is no blanket visual-acceptance switch.

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

The schema-v5 top level contains the full `repository_commit`, `package_version`, bundle and build
manifest SHA-256 values, exact 2016/2025 adapter hashes, and exactly two `runs`. Each run records:

- `autocad_release`, live `product` including normalized and raw ACADVER, exact `adapter`, normalized
  `runtime_series`, embedded `build_commit`, and running `plugin_sha256` identity;
- explicit `licensed=true` and `authorized_test_asset=true` declarations;
- approved `plan_id`, manifest digest, and matching receipt manifest digest;
- source and staged DWG SHA-256 values before and after plotting;
- a path-redacted `template_assets` list. For every external DWG/DWT import it binds the profile id,
  layout, page setup, byte length, approved SHA-256, current company-source SHA-256, and current
  staged-copy SHA-256; an empty list proves that run used no external template asset;
- a path-redacted `published_pdf` record containing the produced PDF's SHA-256, byte length, page
  count, and physical width/height; plus successful receipt state, `publish_verified=true`, and
  proof that the receipt remained readable after restart;
- a path-redacted `visual_reference` record containing the authorized one-page office reference
  PDF's SHA-256, byte length, page count, physical width/height, and bounded comparison tolerance.
  Collection requires that file to be under an allowed root; both collection and later schema
  validation require its orientation/page size to match the `published_pdf` evidence;
- seven explicit visual checks: orientation, crop, viewport scale, lineweights, plot style, fonts,
  and title block;
- the authorized approver and a timezone-qualified completion timestamp.

The validator rejects missing/extra fields, duplicate releases, incorrect adapter/runtime-series
pairs, incorrect ACADVER product identity,
running commit/binary mismatches, changed DWG hashes, a receipt bound to another manifest,
changed/mismatched/redirected template assets, incomplete visual acceptance, or a run that was not
rechecked after AutoCAD restart. A valid report
proves the recorded gates only; the actual evidence files and licensed workstation remain
authoritative.

Schema-v3 final pilot JSON and older run JSON files are intentionally not upgraded in place.
Re-collect both runs with the schema-v5 wheel so the exact visual reference, queue authentication,
and external-template
use or non-use are derived from authorized local files and the immutable job manifest instead of
being supplied manually.

After both runs validate, use the [release acceptance](release-acceptance.md) gate to bind this local
evidence to the exact transferred release kit and produce a sanitized no-overwrite readiness report.
