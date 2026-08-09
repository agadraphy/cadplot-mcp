# Isolated AutoCAD inspection

Opening a DWG through AutoCAD COM can block indefinitely when AutoCAD has a modal dialog, a file
recovery prompt, a missing-reference prompt, or a damaged drawing. A single blocked call must not
freeze the MCP server or an entire batch page.

Every drawing inspection used by `inspect_drawing`, `inventory_office_resources`, planning, and
staging now runs in a fresh helper process:

1. the parent validates the requested `.dwg` against configured `allowed_roots`;
2. a closed schema containing only the exact drawing and allowed roots is sent over standard input;
3. the helper attaches to the already-running AutoCAD instance and uses the existing read-only open,
   inspect, close-without-save contract;
4. the parent accepts only bounded UTF-8 JSON with exact inspection fields and the same resolved
   drawing path;
5. after `inspection_timeout_seconds`, the helper is terminated and the drawing becomes a bounded
   error item instead of blocking the remaining batch page.

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
