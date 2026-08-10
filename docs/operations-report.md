# Restartable operations report

`create_publish_operations_report` is a read-only recovery view over the configured
`workspace_root`. It does not contact AutoCAD, queue work, modify manifests, or delete partial
outputs.

Call it with a limit from 1 to 50. If `has_more=true`, pass `next_after_job_id` as the next call's
`after_job_id`. New job IDs contain a microsecond creation timestamp and therefore do not shift
offset pages. Do not stage more jobs in the middle of one paginated snapshot; finish the current
scan, then start again without a cursor to include later work. Each page has a content-derived
`report_page_id` that can be retained in an operator log.

For licensed release acceptance, `cadplot-collect-recovery` requires an isolated workspace that
contains exactly 2–20 approved completed jobs after AutoCAD restart. It recomputes the complete
operations page and every receipt/output/source/staged identity, then retains the path-redacted
`report_page_id`. A partial page or extra workspace job is rejected instead of being omitted from
the recovery claim.

The report classifies each job:

- `complete`: valid successful receipt whose canonical output-set binding still matches, plus every
  expected PDF structurally valid and an unchanged authorized source DWG;
- `source_changed`: source SHA-256, byte length, or modification timestamp no longer matches the
  staged approval; no queue approval is returned and a new plan/staging job is required;
- `awaiting_execution`: no receipt and no existing outputs; check live plug-in status before using
  the returned exact `queue_approval`;
- `cancelled_hold`: a structurally valid signed-marker envelope exists, so no requeue approval is
  returned; only live plug-in status can authenticate it as `Cancelled`;
- `failed`: valid failure receipt; diagnose its bounded code and stage a new job;
- `manual_review`: partial, invalid, or execution-unverified outputs exist; never overwrite them;
- `invalid_job`: manifest, staged drawing, path boundary, receipt, or digest validation failed.

The summary covers only the current page. A structurally valid PDF replaced after receipt creation
is still `manual_review`, because its output binding no longer matches. A 300-job run is complete
only after every page has been
read through `has_more=false` and every item is `complete`. Presence of a PDF alone is never
execution evidence. To keep MCP responses bounded, at most 20 output issues are included per job;
use `output_issue_count` and `output_issues_truncated` to detect a longer list.
`blank_pdf_page` is an invalid output issue even when page count and MediaBox are correct. A marking
operator only proves that the page is not structurally empty; visual acceptance remains separate.
`pdf_too_large` rejects output above the 128 MiB per-file audit limit. `pdf_changed_during_audit`
means the file identity, size, or timestamp changed while its single audit snapshot was being read;
neither state exposes a SHA-256 or qualifies for execution/output binding.

Every report rebuild re-fingerprints the original allowed-root DWG. This is a currency check, not a
claim that CadPlot modified the source: a user or upstream sync may have produced a newer revision.
Such a job remains auditable, but its old output cannot be called current or queued again in place.

For a live large run, use the plug-in's `queueAvailable` value as the feed window and require
`queueAuthentication=windows-dpapi-current-user+hmac-sha256-v1`. The same status also reports
`queueRecoveredOnStartup`, `queueInterruptedOnStartup`, and `queueCancelledOnStartup`; an interrupted item is a manual-review
boundary, not permission to replay it. If
`queue_publish_batch` returns schema v2 `deferred` items, retain their exact manifest paths, plan
IDs, and manifest digests and retry them after slots reopen. After an AutoCAD restart, live status
revalidates durable pending intents and terminal receipts. Regenerate this report as the workspace
inventory, check live status first, and use a returned `awaiting_execution` `queue_approval` only
when the plug-in does not already report that exact plan. A `job_interrupted` plan must be reviewed
and staged as a new job rather than queued in place.

The queue authentication key is Windows-user-bound state outside this report and workspace. Never
copy, publish, or embed it in acceptance evidence. Moving only the workspace to another user/machine
must leave pending jobs non-executable; preserve the old job for review and create a fresh staged
approval on the authorized target instead.

`get_publish_batch_status` accepts 1 to 20 unique exact plan IDs. It isolates per-plan connection,
not-found, and protocol errors; summarizes `Pending`, `Running`, `Succeeded`, `Failed`, and
`Cancelled`; then
reads one final atomic queue-capacity sample. That queue sample is current at the end of the call,
not a claim that all earlier per-job states were observed in one instant.
