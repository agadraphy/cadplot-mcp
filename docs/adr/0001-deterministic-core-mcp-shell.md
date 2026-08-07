# ADR-0001: Deterministic AutoCAD core behind a narrow MCP surface

**Status:** Accepted  
**Date:** 2026-08-07  
**Deciders:** Demir Eren and project maintainers

## Context

Architecture offices need to apply known layout, viewport scale, page setup, plotter, media, and
plot-style rules to hundreds of revised DWG files. Natural-language instructions are useful, but
the resulting CAD operations must be repeatable, auditable, and safe for production drawings.

Existing local AutoCAD MCP projects either expose low-level drawing primitives against the active
document or use brittle command-line plotting with fixed media and plot-style values. Neither
provides target identity, dry-run planning, allowed-root enforcement, or output validation.

## Decision

CadPlot MCP will use a narrow MCP layer for intent and orchestration, with deterministic adapters
for inspection and CAD execution. The first release is read-only. Future writes will run through an
in-process AutoCAD .NET worker, operate on explicit copies, and require a validated publish plan.

## Options considered

### Python COM for all operations

Low initial complexity and easy packaging, but synchronous COM calls, modal dialogs, apartment
threading, and incomplete plot control make it too fragile as the long-term write engine.

### AutoLISP command dispatcher

Easy deployment, but command prompt sequences vary by AutoCAD version and localization. Generic
LISP execution also creates an unacceptable public MCP security surface.

### Hybrid Python MCP plus AutoCAD .NET worker

The MCP server remains easy to install and client-neutral. AutoCAD's in-process API provides
explicit document transactions and plot/page-setup APIs. It costs more to package across AutoCAD
versions, but gives the strongest production boundary.

## Consequences

- The project can ship useful read-only inspection before write support.
- Tests can use deterministic models without requiring AutoCAD in CI.
- Live AutoCAD integration tests remain opt-in on Windows.
- Supporting several AutoCAD releases may require versioned .NET builds.
- Remote ChatGPT support is a later transport concern, not part of the CAD engine.

## Action items

1. Implement allowed-root DWG discovery and paper-label parsing.
2. Implement read-only layout/frame inspection against an explicitly named DWG.
3. Define a signed/hashed dry-run plan schema before adding writes.
4. Build the .NET worker and copy-only publisher.
5. Validate generated PDFs by existence, byte size, and page count.

