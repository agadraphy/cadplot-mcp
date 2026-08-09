## Summary

Describe the focused behavior change and why it is needed.

## Safety impact

- Source DWGs remain immutable: yes / no / not applicable
- Existing PDFs remain no-overwrite: yes / no / not applicable
- Exact plan and manifest approvals remain enforced: yes / no / not applicable
- New network, process-launch, command-execution, or filesystem surface: none / explain

## Evidence

- [ ] `uv run ruff check .`
- [ ] `uv run pytest`
- [ ] Relevant .NET build/tests, if changed
- [ ] Synthetic evidence is labelled synthetic
- [ ] Live AutoCAD claims identify the licensed release and authorized test asset
- [ ] Documentation and changelog updated where behavior changed
- [ ] No company DWG/PDF/DWT/PC3/PMP/CTB/STB, credentials, or Autodesk binaries are included
- [ ] External GitHub Actions remain full-SHA pinned with read-only permissions and no persisted checkout token

## Known limitations

List anything not proven by this change. Do not present compile-only or synthetic checks as live
AutoCAD evidence.
