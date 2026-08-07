# Third-Party Notices

`cadplot-mcp` is distributed under the MIT License. Its Python dependencies
are installed separately through the package manager and retain their own
licenses and notices.

Current direct dependencies include:

- MCP Python SDK (`mcp`)
- Pydantic (`pydantic`)
- pypdf (`pypdf`, BSD-3-Clause)
- PyYAML (`pyyaml`)
- pywin32 (optional, Windows/AutoCAD integration)

Development dependencies include pytest and Ruff.

The .NET test/build projects use Microsoft.NETFramework.ReferenceAssemblies,
Microsoft.NET.Test.Sdk, xUnit, and the xUnit Visual Studio runner as development-only
NuGet dependencies. Autodesk managed API assemblies are supplied locally by the
developer for compilation and are not included in this repository or its release bundle.

This repository does **not** redistribute Autodesk software, AutoCAD binaries,
Autodesk company assets, sample drawings, fonts, or license material. AutoCAD
and Autodesk are trademarks of Autodesk, Inc.; use of those names here is only
to describe interoperability and does not imply endorsement or affiliation.

For the complete, version-specific third-party license set, inspect the locked
dependency metadata and the installed distributions used for a release.
