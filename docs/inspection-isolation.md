# Isolated AutoCAD inspection

Opening a DWG through AutoCAD COM can block indefinitely when AutoCAD has a modal dialog, a file
recovery prompt, a missing-reference prompt, or a damaged drawing. A single blocked call must not
freeze the MCP server or an entire batch page.

Every drawing inspection used by `inspect_drawing`, `inventory_office_resources`, planning, and
staging now runs in a fresh helper process:

1. the parent validates the requested `.dwg` against configured `allowed_roots`;
2. a closed schema containing only the exact drawing, allowed roots, and already-validated AutoCAD
   ProgID is sent over standard input;
3. the helper attaches to the already-running AutoCAD instance and uses the existing read-only open,
   inspect, close-without-save contract;
4. the parent accepts only bounded UTF-8 JSON with exact inspection fields and the same resolved
   drawing path;
5. after `inspection_timeout_seconds`, the helper is terminated and the drawing becomes a bounded
   error item instead of blocking the remaining batch page.

By default the helper attaches through the version-independent `AutoCAD.Application` ProgID. This
is acceptable only when one intended AutoCAD release is active. Autodesk documents that an
unversioned `GetObject` can return the first AutoCAD instance in the Windows Running Object Table.
For a two-version pilot, set `CADPLOT_AUTOCAD_PROGID` in each MCP process to the matching exact
identity:

```powershell
# AutoCAD 2016 inspection process
$env:CADPLOT_AUTOCAD_PROGID = "AutoCAD.Application.20.1"

# AutoCAD 2025 inspection process
$env:CADPLOT_AUTOCAD_PROGID = "AutoCAD.Application.25.0"
```

CadPlot accepts only the version-independent identity or approved exact 20.1/24.3/25.0
identities, calls `GetActiveObject` without launching AutoCAD, and checks that the returned
application version matches the requested release. A mismatch fails closed before any DWG opens.
`cadplot-doctor --mode full` also compares an exact ProgID to the named-pipe plug-in's
`runtimeSeries`; `inspection_identity_matched=false` prevents a ready result.

AutoCAD 2026 (`25.1`) is rejected because it is outside this project's licensed acceptance scope,
even though Autodesk documents that release as able to use the 2025 managed SDK.

The default is 120 seconds. Configuration accepts only integer values from 5 through 600 seconds.
The helper protocol limits requests to 1 MiB, responses to 8 MiB, layouts/frames to 5,000, named
page setups to 1,000, and warnings to 5,000. Drawing paths are carried in standard input, not
command-line arguments.

Python's `subprocess.run(timeout=...)` kills and waits for the child after the timeout expires.
Operating-system process creation itself cannot be interrupted on every platform, so the deadline
applies once helper startup succeeds; this limitation is not represented as a real-time guarantee.

Terminating the helper does not terminate AutoCAD and never saves a drawing. AutoCAD can still
retain a read-only document or modal prompt after an interrupted COM call; the operator must close
the prompt and inspect that DWG manually before retrying. CadPlot never kills AutoCAD or guesses
that a timed-out drawing is safe.

This timeout is local reliability evidence only. It does not prove that a particular DWG, AutoCAD
release, PC3/PMP, CTB/STB, font set, or title block will publish correctly; those remain
licensed-pilot acceptance gates.

Python reference: [subprocess timeout behavior](https://docs.python.org/3/library/subprocess.html)

Autodesk references:

- [Application Object (ActiveX)](https://help.autodesk.com/cloudhelp/2024/ENU/AutoCAD-ActiveX-Reference/files/GUID-0225808C-8C91-407B-990C-15AB966FFFA8.htm)
- [AutoCAD 2016 COM interoperability](https://help.autodesk.com/cloudhelp/2016/PTB/AutoCAD-NET/files/GUID-BFFF308E-CC10-4C56-A81E-C15FB300EB70.htm)
- [AutoCAD 2025 COM interoperability](https://help.autodesk.com/cloudhelp/2025/PLK/OARX-DevGuide-Managed/files/GUID-BFFF308E-CC10-4C56-A81E-C15FB300EB70.htm)
