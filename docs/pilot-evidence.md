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

Retain the pending-cancellation exercise from `monday-pilot.md` as a separate local operator
transcript: exact cancellation must report `Cancelled` after restart and a running job must return
`job_not_pending`. A cancellation transcript is operational evidence, not proof that a PDF was
published.

After the one-sheet visual pilot, run a clean, isolated batch of 2–20 authorized jobs on that same
AutoCAD release. Wait for terminal receipts, restart AutoCAD, recheck the live plug-in identity, and
collect the entire workspace as one bounded recovery record:

```powershell
cadplot-collect-recovery `
  C:\CadPlotPilot\recovery-2016\pilot-work\job-...-1\manifest.json `
  C:\CadPlotPilot\recovery-2016\pilot-work\job-...-2\manifest.json `
  --release 2016 `
  --approved-by "Authorized CAD manager" `
  --output C:\CadPlotPilot\recovery-2016.json `
  --licensed `
  --authorized-test-assets `
  --restart-verified
```

Use a dedicated workspace containing exactly that approved batch. The collector refuses fewer than
2 or more than 20 manifests, extra workspace jobs, incomplete operations-report paging, missing or
changed source/staged DWGs, nonterminal receipts, unbound outputs, and a mismatched live adapter.
It retains no file paths. Repeat separately for AutoCAD 2025.

First verify the matching-SDK release root, then assemble both distinct run files with its exact
bundle archive and `bundle-build.json`:

```powershell
.\scripts\verify-bundle-release.ps1 -ReleaseRoot C:\CadPlotPilot\bundle-release
cadplot-assemble-pilot `
  --run-2016 C:\CadPlotPilot\run-2016.json `
  --run-2025 C:\CadPlotPilot\run-2025.json `
  --recovery-2016 C:\CadPlotPilot\recovery-2016.json `
  --recovery-2025 C:\CadPlotPilot\recovery-2025.json `
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

The schema-v7 top level contains the full `repository_commit`, `package_version`, bundle and build
manifest SHA-256 values, exact 2016/2025 adapter hashes, exactly two one-sheet `runs`, and exactly
two `batch_recovery` records. Each one-sheet run records:

- `autocad_release`, live `product` including normalized and raw ACADVER, exact `adapter`, normalized
  `runtime_series`, embedded `build_commit`, and running `plugin_sha256` identity;
- explicit `licensed=true` and `authorized_test_asset=true` declarations;
- approved `plan_id`, manifest digest, matching receipt manifest digest, receipt output count, and
  the canonical receipt output-set SHA-256;
- source and staged DWG SHA-256 values before and after plotting;
- a path-redacted `template_assets` list. For every external DWG/DWT import it binds the profile id,
  layout, page setup, byte length, approved SHA-256, current company-source SHA-256, and current
  staged-copy SHA-256; an empty list proves that run used no external template asset;
- a path-redacted `published_pdf` record containing its sheet index, basename, SHA-256, byte length,
  page count, and physical width/height; plus successful receipt state,
  `receipt_output_binding_verified=true`, `publish_verified=true`, and proof that the receipt
  remained readable after restart;
- a path-redacted `visual_reference` record containing the authorized one-page office reference
  PDF's SHA-256, byte length, page count, physical width/height, and bounded comparison tolerance.
  Collection requires that file to be under an allowed root; both collection and later schema
  validation require its effective orientation/page size, including PDF `/Rotate`, to match the
  `published_pdf` evidence;
- seven explicit visual checks: orientation, crop, viewport scale, lineweights, plot style, fonts,
  and title block;
- the authorized approver and a timezone-qualified completion timestamp.

Each path-redacted batch-recovery record binds the same live release/adapter/commit/binary and queue
authentication identity, an exact complete operations-report page ID, an explicit post-restart
attestation, and 2–20 ordered jobs. Every job retains its job/plan/manifest identity, successful
schema-v2 receipt count and output-set digest, independently verified receipt/output binding, and
unchanged source/staged hashes. The assembler requires recovery evidence for both 2016 and 2025 and
matches each running binary to the same verified bundle as its one-sheet run.

The validator rejects missing/extra fields, duplicate releases, incorrect adapter/runtime-series
pairs, incorrect ACADVER product identity,
running commit/binary mismatches, changed DWG hashes, a receipt bound to another manifest or PDF
set,
changed/mismatched/redirected template assets, incomplete visual acceptance, or a run that was not
rechecked after AutoCAD restart. A valid report
proves the recorded gates only; the actual evidence files and licensed workstation remain
authoritative.

Schema-v6 final pilot JSON and older records are intentionally not upgraded in place. Re-collect
both one-sheet runs and both recovery batches with the schema-v7 wheel so receipt/output binding,
visual reference, queue authentication, external-template use, and restart recovery are derived
from authoritative local evidence instead of being supplied manually.

After both one-sheet runs and both recovery records validate, use the
[release acceptance](release-acceptance.md) gate to bind this local evidence to the exact transferred
release kit and produce a sanitized no-overwrite readiness report.
