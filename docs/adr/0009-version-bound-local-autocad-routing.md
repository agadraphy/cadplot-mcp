# ADR-0009: Version-bound local AutoCAD routing

**Status:** Accepted and implemented; licensed acceptance pending
**Date:** 2026-08-10
**Deciders:** CadPlot maintainers

## Context

CadPlot has two independent workstation-local connections: Python uses ActiveX/COM for read-only
DWG inspection, while approved publishing uses the current-user .NET named pipe. The original
defaults were appropriate for one running AutoCAD instance, but they were not a sufficient identity
contract when licensed 2016 and 2025 processes were open together:

- Autodesk documents that version-independent `GetObject("AutoCAD.Application")` can return the
  first AutoCAD instance in the Windows Running Object Table;
- the Python client accepted `CADPLOT_PIPE_NAME`, but the AutoCAD adapters always used the default;
- a named-pipe server created only inside the listener thread could fail invisibly or release its
  name briefly between sequential requests.

The inspector and publisher must not silently address different AutoCAD releases.

## Decision

Use two explicit, independently validated local identities:

1. `CADPLOT_AUTOCAD_PROGID` selects read-only inspection. Accept only
   `AutoCAD.Application` or the approved exact 20.1/24.3/25.0/25.1 ProgIDs. The inspector calls
   `GetActiveObject` and never `CreateObject`, verifies the returned application version, and binds
   the selected identity into the closed parent-to-worker schema.
2. `CADPLOT_PIPE_NAME` selects publishing. Python and both AutoCAD adapters use the same value and
   accept only `[A-Za-z0-9._-]{1,128}`. The current-user server reserves its single pipe instance
   before plug-in initialization returns and reuses that instance across sequential requests.

`cadplot-doctor --mode full` compares an exact COM ProgID to the plug-in `runtimeSeries`. A mismatch
sets `inspection_identity_matched=false` and prevents readiness. The plug-in's existing
adapter/runtime-series check remains the final publish gate.

The version-independent defaults remain available for simple single-AutoCAD operation. When 2016
and 2025 are intentionally active together, each AutoCAD process and matching MCP process must use
distinct pipe names and the matching version-specific COM ProgID.

## Options Considered

### Exact ProgID plus exact current-user pipe

**Pros:** Uses Autodesk's documented version-specific automation identity, remains local, avoids
process IDs in remote tool inputs, and can be verified before any DWG opens or job queues.

**Cons:** Launch environments must be kept consistent and multiple sessions of the same AutoCAD
release still require an operator-selected single session.

### Require all version pilots to run sequentially with defaults

**Pros:** Minimal configuration.

**Cons:** Easy to violate accidentally and cannot prove which release the version-independent COM
registration returned when multiple products are running.

### Accept process IDs or arbitrary ProgIDs from MCP tool calls

**Pros:** Flexible routing.

**Cons:** Expands the remote attack surface, makes approvals depend on volatile process identity,
and permits unrelated COM servers. Rejected.

## Consequences

- Simultaneous 2016/2025 pilots can address inspection and publishing coherently without exposing
  either identity as an unrestricted MCP argument.
- Invalid/foreign ProgIDs, unsafe pipe names, duplicate pipe servers, returned-version mismatches,
  and COM/plug-in cross-release mismatches fail closed.
- Environment selection is fixed when the MCP/AutoCAD process starts; changing it does not retarget
  an already-running process.
- Local tests prove selection, schema binding, server reservation, sequential request reuse, and
  doctor coherence. They do not prove Autodesk ROT behavior or live 2016/2025 publishing; those
  remain licensed acceptance gates.

## Action Items

1. [x] Bind exact ProgID through the isolated inspection worker and verify returned version.
2. [x] Use and validate the same pipe-name contract in Python and both adapters.
3. [x] Reserve/reuse one current-user pipe instance and reject duplicate names.
4. [x] Cross-check exact COM and plug-in runtime identities in full doctor mode.
5. [ ] Retain matching identity evidence in licensed AutoCAD 2016 and 2025 pilot transcripts.

## Autodesk references

- [Application Object (ActiveX)](https://help.autodesk.com/cloudhelp/2024/ENU/AutoCAD-ActiveX-Reference/files/GUID-0225808C-8C91-407B-990C-15AB966FFFA8.htm)
- [AutoCAD 2016 COM interoperability](https://help.autodesk.com/cloudhelp/2016/PTB/AutoCAD-NET/files/GUID-BFFF308E-CC10-4C56-A81E-C15FB300EB70.htm)
- [AutoCAD 2025 COM interoperability](https://help.autodesk.com/cloudhelp/2025/PLK/OARX-DevGuide-Managed/files/GUID-BFFF308E-CC10-4C56-A81E-C15FB300EB70.htm)
