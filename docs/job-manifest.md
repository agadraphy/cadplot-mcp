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

Each expected output also carries the immutable execution specification copied from the approved
plan: target layout, named page setup, plotter, plot style, plot window, rotation, scale
denominator, and drawing-unit conversion. The .NET queue rereads this manifest and cross-checks it
against the queue request before accepting a job.

The manifest starts in `staged` state. A future AutoCAD publisher may only operate on the
`staged_drawing` named in this manifest and must write PDFs under its `output_directory`.

After publishing, call `audit_publish_outputs` with the manifest path. The audit is read-only and
rejects path escapes, duplicate PDF targets, a changed staged DWG, non-PDF headers, and manifests
outside `workspace_root`. Its report includes each valid PDF's byte size and SHA-256 digest.
