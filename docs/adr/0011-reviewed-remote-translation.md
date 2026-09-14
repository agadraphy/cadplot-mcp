# ADR 0011: Reviewed, opted-in remote English copies

Accepted for implementation, 2026-09-14. Extends ADR 0010 only for translation.

The owner explicitly approved cloud transfer of company-approved drawing text.
The closed protocol therefore adds one `translation` command with four discriminated
requests. This is not a generic command/COM/path escape hatch. The legacy protocol
type name `ReadOnlyCommand` includes this extension for compatibility and must not
be interpreted as granting a read-only safety classification to translation apply.

Gateway opt-in, a separate translate scope and per-project workstation consent are
independent gates. Raw text is exempted from generic path-pattern redaction ONLY in
validated translation-content slots. Control fields and every pre-existing tool
retain the original fail-closed redaction. No DWG/PDF/file transfer is added.

The server's immutable successful plan operation supplies owner/client/device,
inventory, exact plan/review and deadline bindings. Atomic operation uniqueness
consumes its approved plan; the worker's durable per-identity/policy/plan journal
prevents replay across tasks and client IDs. Interrupted writes require local
attention unless a verified receipt was already persisted. No blanket retry or
unreviewed overwrite is available.

Local and remote CLI share the MCP services rather than a second CAD implementation.
Target-workstation licensed/visual acceptance remains separate from source-level
verification; it is intentionally not run on the development computer.
