from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

PROPRIETARY_SUFFIXES = {
    ".bak",
    ".ctb",
    ".dwg",
    ".dwt",
    ".dxf",
    ".pmp",
    ".pc3",
    ".stb",
    ".sv$",
}
SECRET_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
SECRET_NAMES = {".env", "claude_desktop_config.json", "config.yaml", "credentials.json"}
AUTODESK_ASSEMBLIES = {"acmgd.dll", "acdbmgd.dll", "accoremgd.dll"}
ALLOWED_BUNDLE_DLLS = {
    "cadplotmcp.autocad2016.dll",
    "cadplotmcp.autocad2025.dll",
    "cadplotmcp.core.dll",
}


def archive_names(path: Path) -> list[str]:
    if path.suffix.casefold() in {".whl", ".zip"}:
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if path.name.casefold().endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return archive.getnames()
    raise ValueError(f"Unsupported release archive: {path}")


def audit_archive(path: Path) -> list[str]:
    violations: list[str] = []
    is_bundle = path.name.casefold().endswith(".bundle.zip")
    names = archive_names(path)
    for name in names:
        normalized = name.replace("\\", "/")
        leaf = Path(normalized).name.casefold()
        suffix = Path(leaf).suffix.casefold()
        if suffix in PROPRIETARY_SUFFIXES:
            violations.append(f"proprietary CAD/plot asset: {normalized}")
        if suffix in SECRET_SUFFIXES:
            violations.append(f"secret-like file: {normalized}")
        if leaf in SECRET_NAMES:
            violations.append(f"local configuration/credential file: {normalized}")
        if leaf in AUTODESK_ASSEMBLIES:
            violations.append(f"Autodesk assembly: {normalized}")
        if suffix in {".dll", ".exe", ".pdb"}:
            if not is_bundle or leaf not in ALLOWED_BUNDLE_DLLS:
                violations.append(f"unexpected binary: {normalized}")

    if not any(Path(name).name == "LICENSE" for name in names):
        violations.append("MIT LICENSE is missing")
    return violations


def find_archives(root: Path) -> list[Path]:
    candidates = [
        *root.glob("*.whl"),
        *root.glob("*.tar.gz"),
        *root.glob("*.bundle.zip"),
    ]
    return sorted(set(candidates))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit CadPlot release archives.")
    parser.add_argument("root", nargs="?", default="dist", type=Path)
    args = parser.parse_args()

    archives = find_archives(args.root)
    if not archives:
        print(f"No release archives found under {args.root}")
        return 2

    failed = False
    for archive in archives:
        violations = audit_archive(archive)
        if violations:
            failed = True
            print(f"FAIL {archive}")
            for violation in violations:
                print(f"  - {violation}")
        else:
            print(f"PASS {archive} ({len(archive_names(archive))} entries)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
