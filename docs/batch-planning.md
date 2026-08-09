# Batch planning

`create_batch_publish_plans` scales read-only inspection to large drawing folders without holding
the entire run inside one MCP call.

- Discovery remains bounded by `max_files` (default 5000).
- One page contains 1 to 50 drawings (default 20).
- Drawings are sorted deterministically by normalized full path.
- Each drawing becomes `ready`, `blocked`, or `error`.
- An AutoCAD error for one DWG does not discard other results on the page.
- Each DWG inspection has the configured subprocess deadline; a timeout becomes that item's
  bounded `error` result and later drawings continue.
- Every page has a deterministic SHA-256 `batch_page_id`.
- The first page returns a metadata-bound `inventory_id`; every later page must repeat it as
  `expected_inventory_id`. Added, removed, renamed, resized, or retimestamped DWGs stop pagination.
- `next_offset` is supplied while more drawings remain.

Example sequence for 300 drawings:

1. call with `offset=0, limit=20` and retain its `inventory_id`;
2. store/review ready plan IDs and blocker messages;
3. call again with the returned `next_offset` and exact `expected_inventory_id`;
4. repeat until `has_more=false`;
5. stage only explicitly approved ready plan IDs.

Batch planning is read-only. It does not imply approval and does not stage or plot any drawing.
If the inventory changes, restart at offset zero and review the newly generated plan IDs.
Resolve any AutoCAD modal prompt before retrying a timed-out item; CadPlot terminates only its helper
and never kills the licensed AutoCAD process.

## Target-count rehearsal

`uv run python scripts/run-synthetic-batch-demo.py --drawings 300` exercises the complete bounded
local path at the requested count. Expected evidence is 15 planning pages, 15 staging batches, 300
unique jobs, six restart-report pages before and after outputs, 300 structurally valid PDFs, and an
unchanged source inventory. The rehearsal never contacts AutoCAD or creates execution receipts;
therefore its required result is `publish_verified=0` and 300 `manual_review` jobs, not live success.
`run-local-preflight.ps1` rejects any result that crosses this evidence boundary.

## Batch staging

`stage_publish_batch` accepts 1 to 20 objects containing exactly `path` and `plan_id`. All paths
and plan IDs must be unique. The full request is structurally validated before the first copy is
created. Each drawing is then re-inspected and re-hashed; a blocker, changed plan ID, or AutoCAD
error is isolated to that item. Successful items create independent job folders. The tool never
plots and never edits an original drawing.
