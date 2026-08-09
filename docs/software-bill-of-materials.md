# Software bill of materials

CadPlot release and demo kits contain `cadplot-mcp.cdx.json`, a CycloneDX 1.7 JSON software bill
of materials (SBOM). CycloneDX 1.7 is the current specification version and `*.cdx.json` is a
recognized filename pattern; see the official [specification overview](https://cyclonedx.org/specification/overview/)
and [JSON reference](https://cyclonedx.org/docs/1.7/json/).

The SBOM is generated only from a passing, current dependency audit. It records:

- the exact Git commit and package version;
- the current `uv.lock` and exported production-requirements SHA-256 values;
- every locked Python runtime package and its declared license;
- SHA-256 values for the exact release artifacts in that kit;
- an explicit `incomplete` composition because the licensed AutoCAD host, platform libraries, and
  non-redistributed Autodesk API assemblies are outside the package.

It deliberately excludes workstation paths, secrets, company DWG/DWT/PC3/PMP/CTB/STB assets,
Autodesk binaries, and test-only dependencies. It also retains
`autocad-launched=false` and `live-publish-proven=false`; an SBOM is inventory evidence, not a live
pilot result.

## Generate and validate

The release builders generate the file automatically. For a standalone audited build:

```powershell
uv run cadplot-sbom generate `
  --dependency-audit <preflight-report.json> `
  --commit <40-character-clean-commit> `
  --version 0.1.0 `
  --artifact wheel=<wheel-path> `
  --artifact source-archive=<source-zip-path> `
  --output <new-output-directory>\cadplot-mcp.cdx.json

uv run cadplot-sbom validate <sbom-path> `
  --commit <40-character-clean-commit> `
  --version 0.1.0
```

Generation and validation refuse redirected input/output files, oversized JSON, duplicate
components, altered dependency graphs, invalid artifact hashes, local paths, and overwrite targets.
The deterministic UUIDv5 serial changes when the exact component inventory changes.

The embedded PowerShell kit verifiers independently check the SBOM identity, release binding,
component counts, artifact hashes, false live-evidence flags, path redaction, and kit-manifest hash.
An organization may additionally validate the file against the official
[`bom-1.7.schema.json`](https://cyclonedx.org/schema/bom-1.7.schema.json) with its approved CycloneDX
or JSON Schema tool.

## Code signing boundary

The SBOM does not claim Authenticode signing. Production DLL signing requires an organization-owned
code-signing certificate, private-key custody policy, timestamp authority, and signed-binary pilot.
That remains an external release gate, separate from local reproducibility and SBOM validation.
