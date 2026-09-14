# Remote translation: scoped MCP and CLI

This extension permits company-approved drawing **text** to cross the gateway.
It never uploads a DWG, accepts a workstation path, selects COM settings remotely,
or enables plotting. The original seven public tools retain their behavior. The
translation extension is disabled unless each deployment/workstation gate is met.

## Deployment order

1. Back up the managed database using the operator's approved recovery procedure.
   With the existing migration identity, run `cadplot-gateway-migrate` from the new
   gateway package. Migration `0005_translation_tasks.sql` adds the closed translation
   task and explicit unknown-write state, preserving immutable tasks, tenant RLS,
   append-only events and idempotency. Do not give DDL rights to the runtime identity.
2. Deploy the new gateway and protocol together. Set
   `CADPLOT_GATEWAY_TRANSLATION_ENABLED=true` only after company text-sharing approval.
   Keep TLS, OAuth, tenant binding and the existing resource audience unchanged.
3. Configure the identity provider to grant **both** the existing read authorization
   scope and `cadplot.translate` only to approved users/clients. A read token alone
   cannot scan text, submit decisions, approve a copy or retrieve translation results.
   For clients relying on default scopes, the identity provider administrator must
   configure this deliberately; do not infer permission from `tools/list` visibility.
4. Update the enrolled Windows worker and its bundled protocol. In its PRIVATE
   worker YAML add `translation_project_ids: [prj_<approved-project-uuid>]`, containing
   only IDs already listed in that worker's `projects`. This is local consent to share
   those projects' text; the gateway cannot add to the list. Set
   `CADPLOT_ENABLE_TRANSLATION=1` in the worker's environment. The CadPlot config needs
   approved drawing roots and a separate private `workspace_root`.
5. Restart the worker in the interactive session containing licensed full AutoCAD.
   Save and close the target DWG, leaving AutoCAD running. No AutoCAD startup, company
   drawing test or unattended acceptance is performed by installation.
6. Verify the opted-in public catalog has 11 tools and perform the licensed drawing
   acceptance on the target computer. Code/unit-test success is not that evidence.

Public deployment is not automatic merely because a commit exists on GitHub.
Render's `checksPass` trigger still requires successful CI. Do not claim the live
service is updated until its deployment and migration are verified.

## MCP workflow

- `scan_drawing_texts(drawing_id, inventory_id?, offset=0, limit=25)` queues a
  page. Poll `get_operation`; review every record and span. Follow `next_offset`
  with the same inventory ID. Context and raw CAD formatting are returned as
  untrusted data, not commands. ASCII Turkish is reviewed like any other text.
  If a page exceeds the bounded result size, retry scanning with a smaller `limit`;
  no truncated page is returned.
- `submit_drawing_translations(drawing_id, inventory_id, decisions,
  expected_review_id?)` accepts 1–100 exact decisions. Use a small batch (CLI max
  128 KiB). Poll completion before the next batch and pass the returned `review_id`.
  Exact retries are accepted; conflicting concurrent review revisions are rejected.
  English/names/codes also need unchanged decisions. Ambiguity blocks applying.
- `create_drawing_translation_plan(drawing_id, inventory_id, review_id, offset, limit)`
  validates the entire review and drawing. Review every change page and all blockers.
  `ready=false` is not an approval. The completed plan operation's `expires_at`
  is the approval deadline; if expired, create/review a fresh plan operation.
- After explicit user approval, call `apply_drawing_translation(drawing_id,
  inventory_id, review_id, approved_plan_id, plan_operation_id)`. The gateway requires
  a successful, ready, unexpired plan belonging to the same user, tenant, client,
  workstation, drawing, inventory and review. It atomically deduplicates the write
  operation using the approved plan. The worker independently checks its stored plan,
  current file, local policy and durable write journal before writing.

All four return asynchronous operations. Poll the returned operation; do not submit
a new write merely because the network timed out. Different clients cannot reuse
another client's plan operation. Worker-side plan deduplication also prevents a
second copy if separately authorized clients submit the identical local plan.

## CLI

```text
cadplot logout
cadplot login --translation
cadplot translate-remote scan drw_<uuid> --limit 25
cadplot operations status op_<scan-operation-uuid>
cadplot translate-remote submit drw_<uuid> --inventory sha256:<inventory> --decisions batch.json
cadplot operations status op_<review-operation-uuid>
cadplot translate-remote submit drw_<uuid> --inventory sha256:<inventory> --decisions next-batch.json --previous-review sha256:<review>
cadplot translate-remote plan drw_<uuid> --inventory sha256:<inventory> --review-id sha256:<review>
cadplot operations status op_<plan-operation-uuid>
cadplot translate-remote apply drw_<uuid> --inventory sha256:<inventory> --review-id sha256:<review> --approve sha256:<plan> --plan-operation op_<plan-operation-uuid>
```

Replace placeholders with complete IDs. `batch.json` is a UTF-8 JSON array of exact
`span_id`, `source`, `source_language`, `target` decisions. Its local filename is
never sent; only the explicitly selected decisions are transmitted. Login may need
fresh provider consent; it never manufactures the translate scope itself.

## Output, recovery and data handling

Successful apply returns an opaque `output_id` and SHA-256, not a workstation path
or download URL. On the worker the result is in
`workspace_root/<output_id>/<source-name>.EN.dwg` with `verified-receipt.json` and a
local audit record. The English file must be visually checked for overflow and
terminology. `full_geometry_proven=false` remains deliberate.

The worker stores reviews, immutable plans and write-start/receipt records in
`<state-database-stem>.translation.sqlite3`, next to its private state database.
A write-start without a matching receipt becomes `ATTENTION_REQUIRED` and is not
automatically replayed, including after a process restart. A verified cached receipt
returns the same output identity. Do not delete the journal to force a retry. An
operator must inspect retained local recovery copies/receipts before reconciliation.

Text, decisions and exact changed strings are company data: the gateway's existing
operation database retains them. Operation expiry is an authorization deadline,
**not deletion or a retention policy**. Enable this only with an approved retention,
backup/access policy and a suitable provider. TLS protects transport; tenant RLS and
scopes protect retrieval. Raw errors/credentials/COM exceptions are not returned.
Local translation has no Render egress, but an MCP host model still sees returned text.

Disable by removing local project consent or the worker enable flag, and by setting
the gateway enable flag false. Scope revocation also blocks future requests and
translation-result retrieval. Existing in-flight AutoCAD calls cannot be undone by
changing a flag; original DWGs are still not overwritten.

Coverage exclusions in [drawing translation](drawing-translation.md#coverage-and-limits)
apply equally to local and remote modes. Tables/xrefs/proxies are not silently ignored.
