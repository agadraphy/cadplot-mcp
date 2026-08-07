# Licensed pilot evidence

Live acceptance is complete only when one AutoCAD 2016 run and one AutoCAD 2025 run are recorded
in a local JSON file and pass:

```powershell
uv run python scripts/validate-pilot-evidence.py C:\CadPlotPilot\pilot-evidence.json
```

Keep the completed evidence under the company's approved audit location; do not commit internal
names, paths, drawings, PDFs, or approvals to the public repository.

## Collect without manual hash copying

After the one-sheet job has succeeded, AutoCAD has been restarted, the persistent receipt has been
rechecked, and the authorized CAD reviewer has accepted all seven visual checks, collect each run:

```powershell
uv run python scripts/collect-pilot-run.py C:\CadPlotPilot\2016-job\manifest.json `
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
enabled, audits `publish_verified=true`, re-hashes source/staged files, requires exactly one PDF,
and refuses to overwrite an existing evidence file. The four declaration flags are human
attestations; do not pass them before the corresponding checks are actually complete.

Assemble both distinct run files with the exact verified bundle and full repository commit:

```powershell
$commit = git rev-parse HEAD
uv run python scripts/assemble-pilot-evidence.py `
  --run-2016 C:\CadPlotPilot\run-2016.json `
  --run-2025 C:\CadPlotPilot\run-2025.json `
  --bundle C:\CadPlotPilot\CadPlotMcp.bundle.zip `
  --repository-commit $commit `
  --output C:\CadPlotPilot\pilot-evidence.json

uv run python scripts/validate-pilot-evidence.py C:\CadPlotPilot\pilot-evidence.json
```

The assembler computes the bundle SHA-256, revalidates both runs, requires distinct 2016/2025
evidence, and also refuses to overwrite its output.

The top level contains `schema_version`, the full 40-character `repository_commit`, the verified
bundle ZIP's `bundle_sha256`, and exactly two `runs`. Each run records:

- `autocad_release`, live `product` string including ACADVER, and exact `adapter` identity;
- explicit `licensed=true` and `authorized_test_asset=true` declarations;
- approved `plan_id`, manifest digest, and matching receipt manifest digest;
- source and staged DWG SHA-256 values before and after plotting;
- produced PDF SHA-256, successful receipt state, `publish_verified=true`, and proof that the
  receipt remained readable after restart;
- seven explicit visual checks: orientation, crop, viewport scale, lineweights, plot style, fonts,
  and title block;
- the authorized approver and a timezone-qualified completion timestamp.

The validator rejects missing/extra fields, duplicate releases, incorrect adapter/ACADVER pairs,
changed DWG hashes, a receipt bound to another manifest, incomplete visual acceptance, or a run
that was not rechecked after AutoCAD restart. A valid report proves the recorded gates only; the
actual evidence file and licensed workstation remain authoritative.
