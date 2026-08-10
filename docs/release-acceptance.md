# Release acceptance

After both licensed AutoCAD one-sheet runs and both per-release small-batch recovery records pass,
bind their schema-v8 pilot evidence to the exact transferred release kit. First run the kit's
embedded verifier, then create a new sanitized acceptance report:

```powershell
$releaseRoot = "C:\CadPlotTransfer\cadplot-release-kit-0.1.0-abcdef0"
$commands = "C:\Users\operator\AppData\Local\CadPlotMcp\python\0.1.0-abcdef0\bin"

& "$releaseRoot\CadPlotMcp.release\scripts\verify-release-kit.ps1" `
  -ReleaseRoot $releaseRoot
& "$commands\cadplot-acceptance.cmd" finalize `
  --release-root $releaseRoot `
  --pilot-evidence C:\CadPlotPilot\pilot-evidence.json `
  --output C:\CadPlotPilot\release-acceptance.json
```

The finalizer independently re-hashes the exact release directory and ZIP entries, wheel, bundle,
bundle-build manifest, and pilot evidence. It revalidates the matching `R20.1`/`R25.0` SDK identities,
both licensed one-sheet runs, both exact-workspace post-restart recovery records, running adapter
hashes, exact commit, and package version. The sanitized schema-v2 report explicitly retains only
the accepted recovery releases and per-release job counts; it contains no
approver name, drawing path, drawing/PDF hash, job identity, or company asset. The finalizer also
rejects CAD/plot/PDF assets and Autodesk managed API DLLs in the public kit or its source archive,
even if a modified manifest attempts to re-hash them. It records `licensed_live_pilot_ready=true`
and `live_publish_proven=true`, while leaving
`public_release_ready=false` until both publication approvals are explicit.

Outer/inner release manifests, pilot evidence, and a retained acceptance report are parsed and
hashed from bounded stable byte snapshots. The release ZIP receives a two-pass streaming fingerprint
before and after exact entry inspection. Every manifest-listed kit file is fingerprinted before use,
then rechecked with the exact kit tree at the end. Redirects, late additions, in-flight replacement,
or same-size/mtime-restored mutation fail closed before any live/public readiness result is accepted.

Only after the company authorizes publication of the sanitized result and the maintainer reviews the
release artifacts, repeat to a new filename with both declarations:

```powershell
& "$commands\cadplot-acceptance.cmd" finalize `
  --release-root $releaseRoot `
  --pilot-evidence C:\CadPlotPilot\pilot-evidence.json `
  --output C:\CadPlotPilot\release-acceptance-approved.json `
  --company-publication-approved `
  --maintainer-release-approved
```

This is a human approval boundary. Do not set either flag by inference. The command never overwrites
an existing report, launches AutoCAD, changes the release kit, or copies the pilot evidence into the
public artifact.

Revalidate later against the retained authoritative inputs:

```powershell
& "$commands\cadplot-acceptance.cmd" validate `
  --release-root $releaseRoot `
  --pilot-evidence C:\CadPlotPilot\pilot-evidence.json `
  --acceptance C:\CadPlotPilot\release-acceptance.json
```

Retain the full pilot evidence only in the company's approved audit location. If a release artifact
or pilot record changes, validation fails rather than updating the acceptance report.
