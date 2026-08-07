# Synthetic end-to-end demo

Run this before the licensed-workstation pilot:

```powershell
uv run python scripts/run-synthetic-demo.py
```

The demo uses a temporary folder and synthetic bytes explicitly marked as not being a real DWG.
It does not connect to or launch AutoCAD. It exercises:

1. safe config loading and allowed-root policy;
2. content fingerprinting;
3. deterministic paper/profile/layout/scale planning;
4. explicit plan-ID approval and copy-only staging;
5. creation of a synthetic one-page A4 PDF;
6. structural, page-count, physical-size, and SHA-256 output auditing;
7. confirmation that the original synthetic source stayed unchanged.

Success is evidence for the platform-independent workflow only. It is not evidence that an
AutoCAD plug-in loaded, a real DWG was interpreted, company plot resources matched, or AutoCAD
produced a PDF. Those remain licensed-workstation acceptance gates.
