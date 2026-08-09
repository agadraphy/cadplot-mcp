# AutoCAD publish executor

The shared executor is compiled into both version adapters only when their matching Autodesk
managed references are supplied. It does not run on a pipe thread. The pipe validates and queues a
job; AutoCAD's `Application.Idle` event drains one job at a time on the application context.

## Execution contract

For every job the plug-in:

1. requires the trusted `CADPLOT_WORKSPACE_ROOT` configured outside the MCP request;
2. revalidates the approved manifest SHA-256, paths, exact plan ID, sheet metadata, source size,
   and staged-DWG SHA-256;
3. independently replays the manifest's window/paper/orientation/scale math and refuses existing
   output PDFs, existing target layouts, unsupported paper units, missing page setups,
   plotter/style/media mismatches, any layout plot scale other than verified 1:1, and a busy
   PlotEngine;
4. opens only the staged DWG copy and makes it the current locked document;
5. creates a unique paper-space layout, clones the explicitly approved in-drawing template, or
   imports only the hash-bound job-local copy of an approved external DWG/DWT layout; it copies the
   approved named page setup and configures a locked viewport centered on the approved model window
   at the approved physical scale;
6. temporarily forces foreground plotting (`BACKGROUNDPLOT=0`) and plots every layout through the
   nested PlotEngine lifecycle to a unique owned `.partial.pdf` in the same job output directory;
7. closes the DWG without saving, so the staged file remains byte-identical, then rechecks every
   final target and promotes all non-empty temporary PDFs without overwrite;
8. restores the user's prior `BACKGROUNDPLOT` value;
9. atomically writes an immutable, manifest-digest-bound terminal `receipt.json` and exposes only
   bounded error codes through job status. Raw exception messages are not returned.

The executor does not run arbitrary AutoCAD commands or AutoLISP. It does not accept a source path,
workspace root, plotter, layout name, or output path beyond the independently validated staged
manifest.

## Opt-in gate

Publishing is disabled by default. Both variables must be present in the environment that launches
AutoCAD:

```powershell
$env:CADPLOT_WORKSPACE_ROOT = "C:\CadPlot\jobs"
$env:CADPLOT_ENABLE_PUBLISH = "1"
```

Restart AutoCAD after changing them. `get_autocad_plugin_status` reports normalized
`runtimeSeries`, `runtimeSupported`, and `publishEnabled`. The 2016 adapter enables publishing only
on `R20.1`; the 2025 adapter enables it only on `R25.0` or `R25.1`. Raw values such as
`20.1s (LMS Tech)` are normalized before the fail-closed adapter check.

## Evidence boundaries

`scripts/probe-autocad-api.ps1` first reads all three managed assembly identities, then compiles the
shared source with the release-appropriate runtime target: `R20.1` uses `net45`, `R25.0`/`R25.1`
uses `net8.0-windows`, and supported intervening releases use `net48`. Unknown series fail closed.
It never launches AutoCAD. A passing probe proves API signatures only. It does not prove:

- that the 2016 or 2025 bundle was built with its matching SDK;
- that the bundle loads in AutoCAD;
- that a company PC3/PMP, CTB/STB, or named page setup resolves;
- that viewport orientation and output appearance match the office reference;
- that a real PDF was produced.

Those claims require the licensed-workstation pilot and an authorized test drawing.

## Current limitations

- Exact approved queue intent is job-local and durable. On startup, never-started pending jobs are
  authenticated by a 256-bit HMAC key stored outside the workspace and protected for the current
  Windows user with DPAPI. Unsigned, altered, or foreign-key markers cannot authorize execution.
  Never-started pending jobs are revalidated and restored with their original request identity. A
  job that had a started marker but no terminal receipt becomes `Failed/job_interrupted` and is
  never replayed automatically.
  Valid terminal receipt state is restored into live status. Corrupt, redirected, over-capacity,
  or identity-mismatched recovery data disables publishing with the bounded
  `publish_queue_initialization_failed` status.
- Queue authorization is intentionally non-portable: copying a workspace to another user or machine
  preserves review evidence but not permission to resume pending publishing. Missing or corrupt key
  state fails closed; the protected key/path is never copied into a demo or release artifact.
- Queue telemetry reports pending-slot capacity, pending, running, and available counts. A bounded
  batch stops after its first `queue_full` response and returns the untouched remainder as
  retryable `deferred` approvals; it never disguises capacity backpressure as terminal failure.
- A plot, layout, or DWG-discard failure removes the executor-owned temporary PDFs and exposes no
  final output names. Multi-file promotion is not a filesystem-wide atomic operation: after a rare
  I/O or external race, the executor reverses only unchanged, SHA-256-matched completed moves
  without overwrite. If Windows also refuses that rollback or a promoted file changed, some finals
  can remain untouched and the receipt records
  `output_commit_partial`; stage a new job instead of editing or retrying in place.
- If Windows refuses cleanup of an owned `.partial.pdf`, the dot-prefixed temporary file may remain
  for manual inspection, but it is never accepted as a manifest output or live success.
- The first live gate is intentionally one sheet. Large batches are enabled only after both
  supported-version pilots accept scale, orientation, crop, fonts, and plot style.
- Without `template_layout`, the generic executor creates an empty layout with one full-sheet
  viewport. With it, the executor preserves an approved in-drawing or hash-bound job-local external
  title block and requires exactly one read-only-inspected and runtime-verified floating viewport.
  External import has compile-only coverage until the separate licensed 2016/2025 pilots accept it.

## Autodesk references

- [Layouts (.NET)](https://help.autodesk.com/cloudhelp/2017/ENU/AutoCAD-NET/files/GUID-5FA86EF3-DEFD-4256-BB1C-56DAC32BD868.htm)
- [Plot settings and page setups (.NET)](https://help.autodesk.com/cloudhelp/2021/ENU/OARX-DevGuide-Managed/files/GUID-56BD3247-471C-4471-A238-FFDFDC3BD2E4.htm)
- [Standard plot scale (ActiveX)](https://help.autodesk.com/cloudhelp/2025/ENU/AutoCAD-LT-ActiveX-Reference/files/GUID-E8D9D4F5-24C1-4C89-924E-DF57C7F0CF5F.htm)
- [Create paper-space viewports (.NET)](https://help.autodesk.com/cloudhelp/2016/ENU/AutoCAD-NET/files/GUID-61C22902-F63B-4204-86EC-FA37312D1B6E.htm)
- [Viewport custom scale](https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_Viewport_CustomScale.html)
- [PlotEngine lifecycle](https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_PlottingServices_PlotEngine.html)
- [LayoutManager.CloneLayout](https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_LayoutManager_CloneLayout_string_string_int.html)
