# OpenAI public submission preparation

This document is a preparation runbook, not evidence that CadPlot is ready for public submission,
review, approval, or publishing. The public gateway and its production controls must be implemented,
deployed, tested, and independently verified before any readiness claim is made.

The current requirements in this runbook are based on the official OpenAI documentation for
[plugin submission](https://developers.openai.com/plugins/deploy/submission),
[submission validation](https://developers.openai.com/plugins/deploy/submission-errors),
[MCP server review](https://developers.openai.com/plugins/deploy/app-review),
[authentication](https://developers.openai.com/plugins/build/auth), and
[plugin guidelines](https://developers.openai.com/plugins/app-guidelines). Recheck those pages at
the start of every submission attempt because portal requirements can change.

## Current scope and status

The only proposed public phase-1 MCP surface is:

1. `list_workstations`
2. `list_projects(workstation_id)`
3. `validate_environment(workstation_id)`
4. `scan_drawings(project_id)`
5. `inspect_drawing(drawing_id)`
6. `create_publish_plan(drawing_id)`
7. `get_operation(operation_id)`

All seven tools are CAD- and user-file-read-only. They may list authorized opaque references,
inspect CAD metadata, compute a dry-run plan, or retrieve an existing read operation. They must not
stage drawings, queue publish jobs, invoke plotting, create PDFs, cancel work, overwrite files, or
expose workstation paths. Under MCP annotation semantics, only `list_workstations` is a pure read.
The five queue tools and `get_operation` set `readOnlyHint=false` and `idempotentHint=false` because
they can persist private operation, dispatch, derived catalog, or deadline-transition state.

CadPlot is model-agnostic and does not bundle or choose an AI model. The connected MCP host/client
selects a compatible model and remains responsible for its model availability and tool support.

The local 20-tool stdio/loopback MCP surface is not the public phase-1 surface. It must not be
published through a generic reverse proxy or included in a public OpenAI tool scan.

Public publish/write features remain disabled. They may be considered only in phase 2 after their
approval-bound gateway and worker controls are implemented and licensed AutoCAD 2016 and AutoCAD
2025 acceptance, recovery, and release-artifact tests have passed. Phase-1 preparation does not
prove phase-2 readiness and must not be described as doing so.

## Public package hold

Do not commit a public `.codex-plugin/plugin.json`, `.mcp.json`, logo, composer icon, screenshots, or
other listing assets yet. Those artifacts must wait until all of the following are real and verified:

- the selected publisher identity;
- the production HTTPS MCP origin;
- the public website, support, privacy-policy, and terms-of-service URLs;
- the final product name, listing copy, and production brand assets.

Do not put placeholder URLs, example domains, invented contact details, or an unverified identity in
a manifest. The existing local integration package can remain local; this hold applies to a future
public-submission package. Do not add `.app.json` for the public submission: an MCP-backed plugin is
submitted with **With MCP** and the production server is scanned directly.

The two JSON files beside this runbook are review working records only. They are not a manifest,
server registration, portal submission, or approval record, and they must never contain credentials,
tokens, local paths, or customer data.

## Current portal constraints to recheck

The current OpenAI submission error reference specifies these final-directory limits. Treat them as
a point-in-time checklist and recheck the official page before creating the held-back package:

- Package name: required, at most 64 characters, begins with an ASCII letter or digit, and contains
  only ASCII letters, digits, `_`, and `-`.
- Version: required semantic version, at most 64 characters.
- Display name: required, one line, at most 30 characters.
- Short description: required, one line, at most 30 characters.
- Long description: required, at most 4,000 characters.
- Developer name: required, one line, at most 80 characters, and aligned with the selected verified
  publisher identity.
- Category: required and selected from the portal's supported categories.
- Capabilities: at most 20; every entry is non-empty, one line, and at most 120 characters.
- Starter prompts: at most 3; every prompt is non-empty, unique after normalization, one line, at
  most 128 characters, and contains no app `@mention`.
- Website, support, privacy-policy, and terms-of-service URLs: all required for an MCP-backed
  submission, HTTPS, and at most 1,024 characters each.
- Optional light and dark brand colors: six-digit hex values meeting the documented 2:1 contrast
  rules.
- Logo and composer icon: both required, square, supported PNG/JPEG/WebP/SVG formats, no larger than
  5 MiB, and within the documented 48-by-48 through 4,096-by-4,096 dimension range.
- Review material: a production HTTPS MCP URL, completed domain verification, current successful
  tool scan, demo-recording URL, exactly five positive cases, exactly three negative cases, release
  notes, verified identity, and required attestations.
- Tool metadata: every tool explicitly sets accurate `readOnlyHint`, `openWorldHint`, and
  `destructiveHint` values and supplies a justification for each. CadPlot also records
  `idempotentHint` for the seven phase-1 tools.
- OAuth review: reviewer-ready demo access is required when authentication is used; credentials stay
  outside the repository.
- Screenshots: permitted only if the scanned MCP server provides custom UI. If custom UI is later
  added, the current rule requires one PNG or JPEG screenshot per starter prompt, exactly 706 pixels
  wide and 400 through 860 pixels tall.

## Repository gates

Complete and retain executable evidence for every item before opening a submission draft:

- Implement a production Streamable HTTP MCP gateway exposing exactly the seven phase-1 tools.
- Keep publishing disabled at the gateway, worker policy, workstation, and AutoCAD plug-in layers.
- Use OAuth 2.1 with PKCE, resource/audience binding, issuer and expiry validation, least-privilege
  scopes, and tenant/user authorization on every call.
- Enforce tenant-scoped workstation, project, drawing, and operation access before resolving any
  local reference.
- Return only opaque identifiers and bounded user-facing CAD summaries. Never return drive-letter,
  UNC, device, manifest, configuration, workspace, or template paths; environment variables; pipe
  names; COM ProgIDs; hostnames; usernames; credentials; stack traces; or raw exceptions.
- Make guessed, stale, revoked, and cross-tenant identifiers fail closed with a generic response
  that does not disclose whether another tenant's resource exists.
- Publish closed input and output schemas, bounded collection sizes, pagination where required, and
  safe retry/expiry behavior for asynchronous read operations.
- Ensure all seven deployed tools advertise the annotation values recorded in
  `openai-tool-annotation-justifications.json`; portal prose cannot override incorrect server
  annotations.
- Execute the five positive and three negative cases in `openai-review-cases.json` against the
  production candidate and retain sanitized results.
- Add automated schema, tenant-isolation, authorization, replay, rate-limit, redaction, pagination,
  and failure-mode tests for the production gateway and worker.
- Complete dependency, vulnerability, secret, and license scans and resolve release-blocking
  findings.
- Verify operations, monitoring, alerting, backups, retention/deletion, incident response, abuse
  handling, support escalation, and rollback procedures.
- Verify the public data inventory against the final privacy policy, including collection purpose,
  recipients, retention, user controls, and deletion handling.
- Preserve the product boundary: CadPlot is MIT-licensed software, but Autodesk software, SDKs,
  trademarks, subscriptions, and licenses are not included. A compatible separately licensed
  AutoCAD installation is required.
- Obtain legal confirmation that the planned Autodesk API use, product naming, trademark wording,
  remote workflow, distribution, and customer licensing model are permitted. Do not imply Autodesk
  affiliation, sponsorship, certification, or endorsement.

No repository gate is satisfied merely because this runbook or the review JSON files exist.

## External identity, domain, and service gates

These steps cannot be completed or proven by repository changes alone:

- Select and verify the individual or business publisher identity in the OpenAI Platform. The
  publisher identity must match the public website, support, privacy, and terms information.
- Use the same OpenAI organization and project that hold the verified identity, and ensure the
  submitter has Apps Management write access.
- Use an OpenAI project with global data residency. The current MCP review documentation states
  that EU data-residency projects cannot submit MCP-backed plugins.
- Own and operate a stable public HTTPS MCP origin that is reachable by OpenAI reviewers. Local,
  loopback, tunnel-only, private-network, staging, and test endpoints are not production endpoints.
- Implement and operate the production OAuth authorization service and protected-resource metadata.
  Configure the scopes and claims required for workspace-domain restrictions if that capability is
  offered.
- Provide a reviewer-ready demo account and sanitized fixtures. The account must work outside the
  private network without MFA, SMS, email confirmation, or reviewer-side configuration. Keep all
  credentials out of the repository.
- Publish real HTTPS website, support, privacy-policy, and terms-of-service pages. Confirm that the
  privacy policy matches the fields returned by the production MCP server.
- Serve the exact portal-issued domain token at `/.well-known/openai-apps-challenge` on the allowed
  MCP host or parent origin, then complete domain verification. Never commit the challenge token.
- Produce a reviewer-accessible demo recording showing the main use cases and tools on every
  supported platform. Store only the final real recording URL in the portal.
- Choose only countries or regions where licensing, support, privacy, and operational coverage are
  ready.
- Prepare accurate release notes and final listing copy in the portal.
- Supply production logo and composer-icon files that meet the current portal format, dimension,
  and size rules. If no custom MCP UI exists, do not submit screenshots. If custom UI is later
  introduced, recheck the screenshot and CSP requirements before creating assets.

## Submission sequence after all gates pass

1. Re-read the official OpenAI submission, error-reference, review, authentication, and policy
   pages and reconcile any changed requirements.
2. Confirm the production server origin, publisher identity, policy URLs, support channel, brand
   assets, demo recording, review account, availability, and release notes are final.
3. Create the final public package and manifest using only those verified values. Validate the
   package locally without changing the deployed tool contract.
4. In the OpenAI Platform, create a **With MCP** submission and select a Universal MCP URL unless
   OpenAI has explicitly approved a Template URL for this publisher and architecture.
5. Enter the production HTTPS MCP URL and authentication details, complete domain verification,
   select **Scan Tools**, and resolve every scan or metadata error before proceeding.
6. Confirm the scan discovers exactly the seven phase-1 tools with the deployed schemas and
   annotations. A changed annotation requires a server fix, redeployment, and a new scan.
7. Enter exactly five positive and three negative cases using the reviewed content in
   `openai-review-cases.json`, plus the current release notes and all required attestations.
8. Run every case with the reviewer account against the same production candidate and retain a
   sanitized evidence record. Do not store prompts after secret or fixture substitution.
9. Submit for review only after all repository and external gates have evidence. Review acceptance
   is not publication.
10. If OpenAI approves the submission, perform the separate portal **Publish** action only after a
    final production, support, policy, and rollback check.

## Go/no-go rule

The decision is **no-go** if any required identity, URL, domain verification, OAuth behavior,
review-account flow, tool scan, annotation, test case, privacy disclosure, legal review, production
operation, or phase-specific acceptance result is missing, stale, failing, or based on an invented
value. Until an approved submission is explicitly published in the portal, CadPlot must not be
described as publicly available through OpenAI.
