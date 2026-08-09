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

For a live large run, use the plug-in's `queueAvailable` value as the feed window. The same status
also reports `queueRecoveredOnStartup` and `queueInterruptedOnStartup`; an interrupted item is a
manual-review boundary, not permission to replay it. If
`queue_publish_batch` returns schema v2 `deferred` items, retain their exact manifest paths, plan
IDs, and manifest digests and retry them after slots reopen. After an AutoCAD restart, live status
revalidates durable pending intents and terminal receipts. Regenerate this report as the workspace
inventory, check live status first, and use a returned `awaiting_execution` `queue_approval` only
when the plug-in does not already report that exact plan. A `job_interrupted` plan must be reviewed
and staged as a new job rather than queued in place.

`get_publish_batch_status` accepts 1 to 20 unique exact plan IDs. It isolates per-plan connection,
not-found, and protocol errors; summarizes `Pending`, `Running`, `Succeeded`, and `Failed`; then
reads one final atomic queue-capacity sample. That queue sample is current at the end of the call,
not a claim that all earlier per-job states were observed in one instant.
