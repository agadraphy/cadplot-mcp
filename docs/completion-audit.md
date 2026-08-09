# Completion audit

This matrix keeps the project objective intact and separates implemented behavior, locally
reproducible evidence, and evidence that can exist only on an authorized licensed workstation.
`local_demo_ready=true` is intentionally not the same claim as production or public-release
readiness.

## Requirement and evidence matrix

| Requirement | Implementation authority | Current reproducible evidence | Status / remaining gate |
|---|---|---|---|
| Never modify source DWGs | path policy, isolated inspector, deterministic planning, copy-only staging, plug-in close/discard path | Python safety/staging tests, source fingerprint checks, synthetic one/300-drawing rehearsals | Implemented and locally verified; authorized pilot must retain before/after source hashes |
| Detect labelled frames and office resources | COM inspector, bounded block/text traversal, paper parser, onboarding inventory | unit tests for polylines, text, attributed/static/nested blocks, conflicts, limits, layouts and page setups | Implemented; representative company DWGs remain a live input-coverage gate |
| Choose the correct layout, page setup, paper, rotation and scale | profile matcher, planner geometry contract, manifest replay, AutoCAD shared executor | Python planner tests, cross-runtime geometry tests, `R24.3` compile-only probe | Implemented; visual scale/crop/style acceptance is required separately on licensed 2016 and 2025 |
| Address the intended AutoCAD release | exact bundle route, COM ProgID selection plus version check, safe named-pipe selection, adapter/runtime check | offline route-tamper rejection, Python COM/worker/doctor tests and current-user named-pipe integration tests; `R25.1`/AutoCAD 2026 is rejected | Implemented locally; exact `20.1`/`25.0` workstation identities must be retained in pilot evidence |
| Publish bounded batches including 300 jobs | paged inventory, max-20 staging/queue/status calls, bounded durable queue and restart operations report | deterministic 300-drawing synthetic rehearsal and 15 durable-queue scenarios | Orchestration verified without AutoCAD; one-sheet then small live batch gates precede an authorized 300-job run |
| Prevent overwrite and ambiguous replay | no-overwrite staging/outputs/receipts, authenticated pending/started/cancel markers, rollback-on-promotion failure | Python/.NET tamper tests, net45 DPAPI runtime probe, restart/cancellation tests | Implemented and locally verified; live PlotEngine interruption behavior still needs both licensed pilots |
| Prove PDFs belong to the successful run | schema-v2 manifest-and-output-bound receipt and structural/physical PDF audit | fixed Python/.NET digest vector, valid-PDF replacement tamper test, restart recovery test | Implemented and locally verified; live receipts/PDFs are external evidence |
| Produce restartable validation reports | receipt reader, per-job PDF audit, paginated operations report, schema-v6 pilot and sanitized final acceptance | unit/contract/CLI tests and no-overwrite evidence validators | Implemented; completed two-version pilot record is intentionally absent until licensed runs pass |
| Install and remove a release safely | isolated clean matching-SDK bundle builder, full-release-bound transactional install/verifiers/uninstallers | stale outputs excluded by construction; installer rechecks sibling ZIP/manifest/commit/matching-SDK evidence; protocol-only install is rejected without test consent | Installer path verified synthetically; real matching-SDK bundle and company-machine install remain external |
| Connect an MCP-capable model safely | closed 20-tool STDIO surface, loopback-only HTTP, tunnel preflight and 13-case tool-selection evaluation | real installed-wheel STDIO/HTTP smokes, header/size guards, canonical evaluation tests | Local target verified; Secure MCP Tunnel provisioning, app scan, workspace permissions and live evaluation are administrator gates |
| Ship as auditable open source | MIT license, Turkish/English docs, changelog, ADRs, source audit, locked dependencies, CycloneDX SBOM | clean source/release audits, dependency/license audit, deterministic wheel/source/SBOM verification | Repository is publication-ready as source material; creating/pushing a public remote and release approvals are explicit maintainer actions |
| Support signed organizational binaries when required | signing is deliberately outside the build and SBOM claims | verifiers preserve `signed=false`/no-live-claim boundaries | Optional external company certificate/timestamp gate; unsigned builds are never represented as signed |

## Canonical local evidence command

From a clean commit on Windows:

```powershell
.\scripts\run-demo-rehearsal.ps1 `
  -DotNet "<dotnet.exe>" `
  -AutoCADApiDir "<installed AutoCAD managed API folder>" `
  -SkipSync `
  -AuditDependencies `
  -WriteReport `
  -ReportPath "<new readiness.json>"
```

The report may set `local_demo_ready=true` only while `autocad_launched=false`,
`live_publish_proven=false`, `licensed_live_pilot_ready=false`, and
`public_release_ready=false` remain explicit. Build the no-overwrite portable delivery from that
exact report with `scripts/build-demo-kit.ps1`, then run both embedded verifiers.

## Required external completion sequence

1. Build and verify one clean bundle with authorized `R20.1` and `R25.0` managed SDK references.
2. Install it transactionally on the licensed workstation and independently verify the install
   receipt.
3. Run separate one-sheet 2016 and 2025 pilots with authorized DWG/resource copies. Require exact
   COM/pipe/adapter identity, unchanged source/staged DWG hashes, schema-v2 output binding,
   `publish_verified=true`, restart recovery, and all seven visual checks.
4. Validate the schema-v6 two-version pilot record, then run a small restart/recovery batch before
   authorizing a large batch.
5. Complete the administrator-controlled tunnel/app scan and the 13-case live model evaluation if
   ChatGPT web is part of the deployment.
6. Finalize sanitized release acceptance. Publication becomes ready only after both company and
   maintainer approvals are explicitly recorded.

Until all applicable steps pass, the correct project state is a locally verified publish candidate,
not a completed licensed deployment.
