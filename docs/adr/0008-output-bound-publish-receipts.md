# ADR-0008: Output-bound publish receipts

**Status:** Accepted
**Date:** 2026-08-10
**Deciders:** CadPlot maintainers

## Context

The original terminal receipt bound a successful AutoCAD run to the approved plan and immutable
job-manifest digest. The independent audit separately checked that every expected PDF existed and
was structurally valid. That separation could not prove that the PDFs being audited were the exact
bytes present when AutoCAD wrote the success receipt: a same-name, structurally valid replacement
could still satisfy both checks.

The public evidence contract must detect post-receipt output replacement without embedding company
paths, unbounded output metadata, or PDF contents in the receipt. The algorithm must also produce
identical results in the .NET Framework 4.5 adapter and the Python audit.

## Decision

Advance terminal publish receipts to schema version 2. A successful receipt records:

- `output_count`: the exact manifest output count;
- `outputs_sha256`: one canonical digest over the complete ordered output set.

The digest starts with the UTF-8 domain bytes `cadplot-receipt-outputs-v1\0`, followed by signed
64-bit big-endian output count. For each output in manifest order it appends signed 64-bit big-endian
sheet index, signed 64-bit big-endian UTF-8 filename length, the basename bytes, signed 64-bit
big-endian file length, and the raw 32-byte PDF SHA-256. Paths are not included.

The receipt writer independently revalidates the manifest and staged DWG after plotting, refuses
missing, empty, redirected, unstable, or count-mismatched outputs, computes the binding, and then
uses no-overwrite receipt creation. Restart recovery and Python auditing independently recompute the
same binding. A successful receipt is valid only while its exact output set still matches. Failed
receipts record `output_count=0` and `outputs_sha256=null`.

Schema-v1 receipts fail closed. They are retained as historical review artifacts but are never
rewritten or upgraded in place; operators create a newly planned and staged job for schema-v2
evidence.

## Options Considered

### Canonical aggregate digest in the receipt

| Dimension | Assessment |
|---|---|
| Cross-runtime determinism | High |
| Receipt size | Constant |
| Path disclosure | None |
| Tamper detection | Exact output set |

**Pros:** Compact, path-redacted, deterministic, and independently reproducible.
**Cons:** Per-file mismatch diagnosis still comes from the normal PDF audit rather than the receipt.

### Store every output hash as a receipt array

**Pros:** Direct per-file inspection.
**Cons:** Receipt size grows with batch size, duplicates manifest/audit data, and expands the strict
cross-runtime schema surface.

### Continue binding only the manifest

**Pros:** No migration.
**Cons:** Cannot distinguish the published bytes from a later structurally valid replacement.

## Consequences

- A missing or replaced PDF invalidates execution evidence and `publish_verified` even if the new
  file is a valid one-page PDF of the expected physical dimensions.
- AutoCAD restart recovery refuses terminal state when the output binding no longer matches.
- A party able to rewrite both the PDF and receipt can calculate a new digest. Receipt authenticity
  therefore still depends on the existing trusted current-user/workspace boundary and trusted
  evidence handoff; this decision adds integrity binding, not a third-party digital signature.
- Pilot evidence advances to schema v6 so the exact receipt count/digest and independent audit result
  are retained with each licensed 2016/2025 run.

## Action Items

1. [x] Implement the same canonical digest in .NET and Python with a fixed cross-runtime vector.
2. [x] Reject changed outputs during .NET restart recovery and Python audit.
3. [x] Add structurally valid PDF replacement tests.
4. [ ] Complete licensed AutoCAD 2016 and 2025 one-sheet pilots using schema-v2 receipts.
