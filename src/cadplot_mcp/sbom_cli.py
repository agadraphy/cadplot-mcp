from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cadplot_mcp.sbom import (
    build_sbom,
    load_sbom,
    require_plain_file,
    validate_sbom,
    write_sbom,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or validate the CadPlot CycloneDX SBOM.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="Generate a non-overwriting release SBOM.")
    generate.add_argument("--dependency-audit", required=True)
    generate.add_argument("--commit", required=True)
    generate.add_argument("--version", required=True)
    generate.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH")
    generate.add_argument("--output", required=True)
    validate = subparsers.add_parser("validate", help="Validate the strict CadPlot SBOM profile.")
    validate.add_argument("sbom")
    validate.add_argument("--commit")
    validate.add_argument("--version")

    args = parser.parse_args()
    try:
        if args.command == "generate":
            audit = _load_dependency_audit(args.dependency_audit)
            artifacts = _parse_artifacts(args.artifact)
            value = build_sbom(
                exact_commit=args.commit,
                package_version=args.version,
                dependency_audit=audit,
                artifacts=artifacts,
            )
            output = write_sbom(args.output, value)
            result = {
                **validate_sbom(value),
                "output": output.name,
                "machine_paths_included": False,
            }
        else:
            value = load_sbom(args.sbom)
            result = validate_sbom(
                value,
                expected_commit=args.commit,
                expected_version=args.version,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


def _load_dependency_audit(path: str) -> dict[str, Any]:
    source = require_plain_file(path)
    if source.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Dependency audit input must be a plain JSON file no larger than 4 MiB.")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Dependency audit input must be valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise ValueError("Dependency audit input must be a JSON object.")
    nested = value.get("dependency_audit")
    if nested is not None:
        if value.get("dependency_audit_ran") is not True or not isinstance(nested, dict):
            raise ValueError("Readiness report contains inconsistent dependency-audit evidence.")
        return nested
    return value


def _parse_artifacts(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path or name in result:
            raise ValueError("Each --artifact must be a unique NAME=PATH value.")
        result[name] = Path(raw_path)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
