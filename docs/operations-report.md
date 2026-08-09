# Restartable operations report

`create_publish_operations_report` is a read-only recovery view over the configured
`workspace_root`. It does not contact AutoCAD, queue work, modify manifests, or delete partial
outputs.

Call it with a limit from 1 to 50. If `has_more=true`, pass `next_after_job_id` as the next call's
`after_job_id`. New job IDs contain a microsecond creation timestamp and therefore do not shift
offset pages. Do not stage more jobs in the middle of one paginated snapshot; finish the current
scan, then start again without a cursor to include later work. Each page has a content-derived
`report_page_id` that can be retained in an operator log.

The report classifies each job:

- `complete`: valid successful receipt plus every expected PDF structurally valid;
- `awaiting_execution`: no receipt and no existing outputs; check live plug-in status before using
  the returned exact `queue_approval`;
- `failed`: valid failure receipt; diagnose its bounded code and stage a new job;
- `manual_review`: partial, invalid, or execution-unverified outputs exist; never overwrite them;
- `invalid_job`: manifest, staged drawing, path boundary, receipt, or digest validation failed.

The summary covers only the current page. A 300-job run is complete only after every page has been
read through `has_more=false` and every item is `complete`. Presence of a PDF alone is never
execution evidence. To keep MCP responses bounded, at most 20 output issues are included per job;
use `output_issue_count` and `output_issues_truncated` to detect a longer list.

For a live large run, use the plug-in's `queueAvailable` value as the feed window. If
`queue_publish_batch` returns schema v2 `deferred` items, retain their exact manifest paths, plan
IDs, and manifest digests and retry them after slots reopen. After an AutoCAD restart, live status
is intentionally empty; regenerate this report and use only fresh `awaiting_execution`
`queue_approval` objects.
