# Autodesk SDK prerequisites

CadPlot MCP does not redistribute Autodesk DLLs. An authorized operator must obtain and accept the
applicable Autodesk ObjectARX SDK terms, then pass the local managed reference directories to the
release build. Do not upload SDK files, company plot resources, or DWGs to this repository.

Required inputs:

- AutoCAD 2016 adapter: ObjectARX/Managed SDK series `R20.1`, targeting .NET Framework 4.5.
- AutoCAD 2025 adapter: ObjectARX/Managed SDK series `R25.0`, targeting .NET 8.
- Each reference directory must contain matching `AcMgd.dll`, `AcDbMgd.dll`, and `AcCoreMgd.dll`.

Autodesk recommends referencing the managed assemblies from the ObjectARX SDK `inc` directory and
setting those references not to copy locally. CadPlot's build independently reads the three assembly
versions and SHA-256 hashes, rejects a mixed or wrong release, and never copies Autodesk assemblies
into the public bundle.

The matching-SDK compiler always uses new release-specific `obj` and `bin` trees under the current
user's temporary directory, disables incremental and shared compilation, verifies all four required
CadPlot DLL outputs, and removes only that exact non-redirected temporary tree. Repository `bin` or
`obj` files from an earlier SDK, compile probe, or protocol-only build are never packaging inputs.

Official references:

- [AutoCAD 2025 managed .NET project setup](https://help.autodesk.com/cloudhelp/2025/PLK/OARX-DevGuide-Managed/files/GUID-8657D153-0120-4881-A3C8-E00ED139E0D3.htm)
- [AutoCAD 2025 compatibility table](https://help.autodesk.com/cloudhelp/2025/ENU/AutoCAD-Customization/files/GUID-C21B8F00-C7DE-4E44-8006-D5DC99199F31.htm)
- [AutoCAD 2026 compatibility table showing release 25.1](https://help.autodesk.com/view/OARX/2026/ENU/?guid=GUID-A6C680F2-DE2E-418A-A182-E4884073338A)
- [AutoCAD 2016 compatibility table](https://help.autodesk.com/cloudhelp/2016/ENU/AutoCAD-Customization/files/GUID-D54B0935-1638-4F97-8B37-1EC3635A1E71.htm)
- [ObjectARX SDK licensing and download](https://aps.autodesk.com/developer/overview/autocad-objectarx-sdk-licensing)

The SDK agreement is an external legal gate. CadPlot never downloads, installs, or accepts it on the
operator's behalf.

The 2025 adapter is intentionally routed only to release `R25.0`. Autodesk lists AutoCAD 2026 as
`R25.1`; compatibility with the 2025 managed SDK is not treated as equivalent to a licensed 2026
acceptance run, so the package loader, COM allowlist, runtime guard, and verifier all reject it.

After the two exact SDK directories are available, a clean source checkout can produce and verify the
full transfer package in one command without launching AutoCAD:

```powershell
.\scripts\build-complete-release.ps1 `
  -AutoCAD2016SdkDir "C:\ObjectARX2016\inc" `
  -AutoCAD2025SdkDir "C:\ObjectARX2025\inc" `
  -DotNet "$env:USERPROFILE\.dotnet\dotnet.exe"
```

The command runs the complete local rehearsal and current dependency audit unless a valid
`-ReadinessReport` is supplied, builds both adapters, verifies the bundle, creates the combined
release kit, then independently verifies that kit. Outputs are never overwritten. Partial outputs
are retained for inspection if a later stage fails. `autocad_launched=false` and
`live_publish_proven=false` remain correct until the separate licensed 2016 and 2025 pilots pass.
