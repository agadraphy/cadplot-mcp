# ADR 0002: Bounded in-process publish queue

## Status

Implemented. Durable restart behavior is amended by ADR 0004 and authenticated pending cancellation
by ADR 0006. Licensed AutoCAD 2016 and 2025 live
acceptance remains pending.

## Context

Named-pipe requests are handled on a background thread, while AutoCAD drawing and plotting APIs
must be coordinated with the application/document execution context. Executing arbitrary pipe
payloads directly against AutoCAD would also allow duplicate, concurrent, or out-of-workspace jobs.

## Decision

The shared .NET core owns a bounded FIFO queue. A request is accepted only when:

- its plan ID is a SHA-256 identifier;
- its manifest is in a direct child job folder of a preconfigured trusted workspace;
- its staged DWG is under that job's `source` folder;
- its output directory is exactly that job's `output` folder;
- the manifest, DWG, and output directory exist;
- manifest plan/job/path identities match the queue request;
- PDF and layout targets are unique and remain inside the job boundary;
- every plot window, rotation, scale, page setup, plotter, and style value is structurally valid;
- the sheet count is between 1 and 5000;
- the plan has not previously been queued in the current plug-in process.

Jobs transition from `Pending` to `Running`, then to `Succeeded` or `Failed`; an exact pending-only
control may instead persist `Cancelled`. Completed or cancelled plan IDs
remain recorded for the process lifetime, preventing accidental duplicate plotting.

Status, queue, and per-job responses expose the configured pending capacity plus current pending,
running, and available counts. Batch response schema v2 classifies `queue_full` and every untouched
approval after it as `deferred`, not `failed`, and makes no more pipe requests in that call. The
caller can wait for `queueAvailable` to increase and retry those same hash-bound approvals without
asking the operator to approve different content.

`PublishJobWorker` drains one request through an `IPublishJobExecutor`. An interlocked busy guard
rejects concurrent processing. Executor exceptions are converted to a failed state containing the
exception type only; exception messages are not returned across the job boundary.

## Consequences

The named-pipe listener enqueues validated requests only. Both version adapters share an
`Application.Idle` scheduler that drains at most one job at a time on AutoCAD's application
context, reports bounded live status, and persists immutable terminal evidence. The queue command
remains disabled unless the operator explicitly enables publishing before AutoCAD starts.

Source-level implementation and managed-API compilation do not replace the separately recorded
licensed AutoCAD 2016 and 2025 pilots.
