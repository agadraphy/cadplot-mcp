# Licensed pilot evidence

Live acceptance is complete only when one AutoCAD 2016 run and one AutoCAD 2025 run are recorded
in a local JSON file and pass:

```powershell
uv run python scripts/validate-pilot-evidence.py C:\CadPlotPilot\pilot-evidence.json
```

Keep the completed evidence under the company's approved audit location; do not commit internal
names, paths, drawings, PDFs, or approvals to the public repository.

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
