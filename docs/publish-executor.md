# AutoCAD publish executor

The shared executor is compiled into both version adapters only when their matching Autodesk
managed references are supplied. It does not run on a pipe thread. The pipe validates and queues a
job; AutoCAD's `Application.Idle` event drains one job at a time on the application context.

## Execution contract

For every job the plug-in:

1. requires the trusted `CADPLOT_WORKSPACE_ROOT` configured outside the MCP request;
2. revalidates the approved manifest SHA-256, paths, exact plan ID, sheet metadata, source size,
   and staged-DWG SHA-256;
3. refuses existing output PDFs, existing target layouts, unsupported paper units, missing page
   setups, plotter/style/media mismatches, and a busy PlotEngine;
4. opens only the staged DWG copy and makes it the current locked document;
5. creates a unique paper-space layout, copies the approved named page setup, and creates a locked
   viewport centered on the approved model window at the approved physical scale;
6. temporarily forces foreground plotting (`BACKGROUNDPLOT=0`), plots each current layout through
   the nested PlotEngine lifecycle to its explicit job PDF, and restores the user's prior value;
7. closes the DWG without saving, so the staged file remains byte-identical;
8. atomically writes an immutable, manifest-digest-bound terminal `receipt.json` and exposes only
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

Restart AutoCAD after changing them. `get_autocad_plugin_status` reports `publishEnabled`.

## Evidence boundaries

`scripts/probe-autocad-api.ps1` compiles the shared source against a selected installed managed API
folder and never launches AutoCAD. A passing probe proves API signatures only. It does not prove:

- that the 2016 or 2025 bundle was built with its matching SDK;
- that the bundle loads in AutoCAD;
- that a company PC3/PMP, CTB/STB, or named page setup resolves;
- that viewport orientation and output appearance match the office reference;
- that a real PDF was produced.

Those claims require the licensed-workstation pilot and an authorized test drawing.

## Current limitations

- The queue and live status history are process-local; restarting AutoCAD clears them. Terminal
  results survive in `receipt.json` and can be cross-checked through `read_publish_receipt`.
- A mid-job failure can leave already-created PDFs in that job. They are never overwritten. Audit
  the job, diagnose the bounded failure code, and stage a new job for a clean retry.
- The first live gate is intentionally one sheet. Large batches are enabled only after both
  supported-version pilots accept scale, orientation, crop, fonts, and plot style.
- The generic executor creates an empty layout with one full-sheet viewport. Offices that require
  paper-space title-block geometry need a separately approved template-import extension.

## Autodesk references

- [Layouts (.NET)](https://help.autodesk.com/cloudhelp/2017/ENU/AutoCAD-NET/files/GUID-5FA86EF3-DEFD-4256-BB1C-56DAC32BD868.htm)
- [Plot settings and page setups (.NET)](https://help.autodesk.com/cloudhelp/2021/ENU/OARX-DevGuide-Managed/files/GUID-56BD3247-471C-4471-A238-FFDFDC3BD2E4.htm)
- [Create paper-space viewports (.NET)](https://help.autodesk.com/cloudhelp/2016/ENU/AutoCAD-NET/files/GUID-61C22902-F63B-4204-86EC-FA37312D1B6E.htm)
- [Viewport custom scale](https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_DatabaseServices_Viewport_CustomScale.html)
- [PlotEngine lifecycle](https://help.autodesk.com/cloudhelp/2022/ENU/OARX-ManagedRefGuide/files/OARX-ManagedRefGuide-Autodesk_AutoCAD_PlottingServices_PlotEngine.html)
