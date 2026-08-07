# ADR 0002: Bounded in-process publish queue

## Status

Accepted for implementation; not yet connected to an AutoCAD publishing adapter.

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
- the sheet count is between 1 and 5000;
- the plan has not previously been queued in the current plug-in process.

Jobs transition from `Pending` to `Running`, then to `Succeeded` or `Failed`. Completed plan IDs
remain recorded for the process lifetime, preventing accidental duplicate plotting.

## Consequences

The named-pipe listener will enqueue validated requests only. A future version-specific AutoCAD
adapter must drain at most one job at a time from AutoCAD's supported application context and must
report completion back to this queue. Until that adapter is implemented and tested with licensed
AutoCAD, no pipe command exposes the queue.
