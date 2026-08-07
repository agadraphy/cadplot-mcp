# Job manifest

`stage_publish_job` is the first write-capable MCP tool. It never edits an original DWG and it
does not invoke AutoCAD plotting. The caller must pass the exact `plan_id` returned by the latest
`create_publish_plan` call.

Before writing, CadPlot MCP:

1. reopens the drawing read-only for inspection;
2. hashes the source DWG before and after inspection;
3. recreates the complete publish plan;
4. compares the caller's approved plan ID;
5. rejects any source-content, page-profile, frame, or plan change;
6. rejects writable workspaces that pass through a symlink or Windows junction.

On success it creates a unique `job-*` directory below `workspace_root` containing:

- `source/<drawing>.dwg`: the verified working copy;
- `output/`: an initially empty directory with collision-free expected PDF names;
- `manifest.json`: job identity, source fingerprint, plan identity, and output states.

After the AutoCAD worker reaches a terminal result, the plug-in atomically creates one immutable
`receipt.json` beside the manifest. It binds `plan_id` and the approved manifest SHA-256 to a
`succeeded` or `failed` state, a timezone-qualified completion timestamp, and (for failure) a
bounded machine-safe error code. It is never overwritten on retry.

The staging response also returns `manifest_sha256`. It is not embedded in the manifest (which
would be self-referential). `queue_publish_job` requires the caller to approve both `plan_id` and
this exact digest. The plug-in verifies the manifest digest when queueing and again immediately
before execution, closing the staging-to-execution time-of-check/time-of-use gap.

Each expected output also carries the immutable execution specification copied from the approved
plan: target layout, named page setup, plotter, plot style, plot window, rotation, scale
denominator, drawing-unit conversion, and optional in-drawing template layout. The .NET queue
rereads this manifest and cross-checks it
against the queue request before accepting a job.
The plot geometry includes the expected physical paper width and height. PDF auditing compares
those values to the parsed PDF MediaBox independent of orientation, using configured
`pdf_page_tolerance_mm`.

The manifest starts in `staged` state. The AutoCAD executor may operate only on the
`staged_drawing` named in this manifest and may write PDFs only under its `output_directory`.

After publishing, call `audit_publish_outputs` with the manifest path. The audit is read-only and
rejects path escapes, duplicate PDF targets, a changed staged DWG, invalid/encrypted PDF content,
and any output that is not exactly one page. Its report includes page dimensions, byte size, and
SHA-256 digest for each valid PDF. `outputs_complete` describes only the expected PDFs;
`execution_verified` requires a valid successful receipt; `publish_verified` is true only when
both are true. `read_publish_receipt` exposes the same cross-checked terminal evidence directly.
