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
schema-v2 `receipt.json` beside the manifest. It binds `plan_id` and the approved manifest SHA-256
to a `succeeded` or `failed` state, a timezone-qualified completion timestamp, and (for failure) a
bounded machine-safe error code. A successful receipt also records the exact output count and a
canonical SHA-256 binding over every ordered sheet index, PDF filename, byte length, and PDF
SHA-256. It is never overwritten on retry.

The staging response also returns `manifest_sha256`. It is not embedded in the manifest (which
would be self-referential). `queue_publish_job` requires the caller to approve both `plan_id` and
this exact digest. The plug-in verifies the manifest digest when queueing and again immediately
before execution, closing the staging-to-execution time-of-check/time-of-use gap.

Each expected output also carries the immutable execution specification copied from the approved
plan: target layout, named page setup, plotter, plot style, plot window, rotation, selected and
derived scale denominators, scale tolerance, drawing-unit conversion, and optional in-drawing
template layout. The .NET queue rereads this manifest, cross-checks it against the queue request,
and independently replays the window/paper/orientation/scale math before accepting a job. A changed
rotation, distorted aspect, inconsistent derived scale, or selected denominator outside the
approved tolerance fails closed before AutoCAD plotting.

Jobs staged by an earlier build that do not contain the complete replay fields must be planned and
staged again after upgrading. The queue deliberately rejects those legacy manifests rather than
guessing missing geometry or tolerance values.
The plot geometry includes the expected physical paper width and height. PDF auditing compares
those values to the parsed PDF MediaBox independent of orientation, using configured
`pdf_page_tolerance_mm`.

The manifest starts in `staged` state. The AutoCAD executor may operate only on the
`staged_drawing` named in this manifest and may write PDFs only under its `output_directory`.
Optional `template_assets` are direct-child DWG/DWT copies under `source/templates`; each carries a
safe profile id, layout/page-setup names, byte length, and SHA-256. Outputs reference them only by id.
Python audit and the plug-in both reject missing, changed, redirected, escaped, duplicate, unused, or
metadata-mismatched assets before importing any layout.
Expected final PDF names stay absent while plotting. Every sheet is first written to a unique
executor-owned `.partial.pdf` beside its final target; only after all plots finish and the staged
DWG closes without saving are non-empty temporary files promoted with no-overwrite moves. A normal
mid-job plot failure therefore leaves no final manifest output. If a later move fails, earlier moves
whose length and SHA-256 still match the pre-promotion temporary files are reversed in order. A
changed file or failed rollback is left untouched and retained as an explicit `output_commit_partial`
result for receipt/audit review.

After publishing, call `audit_publish_outputs` with the manifest path. The audit is read-only and
rejects path escapes, symlink/junction-redirection in the job tree, duplicate PDF targets, a changed
staged DWG, invalid/encrypted PDF content, and any output that is not exactly one page. Each PDF is
limited to 128 MiB and read into one stable byte snapshot. Page structure, geometry, marking
operators, byte length, and SHA-256 are all derived from that same snapshot. A device/file identity,
size, or modification-time change while reading yields `pdf_changed_during_audit`; an oversized file
yields `pdf_too_large`. A correctly sized page is still rejected as `blank_pdf_page` unless its
decoded content contains a path-paint, text-show, shading, image, or form-invocation operator. The
report retains decoded content byte and marking-operator counts; this is a strong blank-page guard,
not a replacement for visual crop/scale/style review. Its report also includes page dimensions,
byte size, and SHA-256 digest for each valid PDF, then independently
recomputes the successful receipt's canonical output-set binding. It re-fingerprints the original
allowed-root source at the end of the audit;
`source_unchanged` requires the approved SHA-256, byte length, and modification timestamp to match.
`outputs_complete` describes only the expected PDFs; `execution_verified` requires a valid
successful receipt whose output binding still matches; `publish_verified` is true only when all
three gates pass. Replacing even a structurally valid PDF, or revising the source after staging,
therefore fails closed. `read_publish_receipt` still exposes the immutable historical terminal
evidence directly, even when a new plan is now required.

Both `validate_staged_job` and `queue_publish_job` run the same source-currency check before opening
the local plug-in pipe. A changed source is never silently substituted into the old staging job;
plan, explicit approval, and copy-only staging must be repeated.

Schema-v1 receipts are intentionally not upgraded or rewritten. Preserve them for review, then
plan, stage, and publish a new job to obtain schema-v2 evidence.
