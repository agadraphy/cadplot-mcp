from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cadplot_mcp.pilot import assemble_pilot_evidence, validate_bundle_build_evidence

MAX_RUN_BYTES = 256 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble distinct 2016 and 2025 pilot runs into final validated evidence."
    )
    parser.add_argument("--run-2016", required=True)
    parser.add_argument("--run-2025", required=True)
    parser.add_argument("--bundle", required=True, help="Verified CadPlot bundle ZIP.")
    parser.add_argument(
        "--bundle-build-manifest",
        required=True,
        help="Matching bundle-build.json from the verified release root.",
    )
    parser.add_argument(
        "--output", required=True, help="New local JSON file; existing files refuse."
    )
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve(strict=False)
    if output.exists():
        return _fail("Output already exists; final pilot evidence is never overwritten.")
    try:
        bundle = Path(args.bundle).expanduser().resolve(strict=True)
        if not bundle.is_file():
            raise ValueError("Bundle path must be a file.")
        bundle_evidence = validate_bundle_build_evidence(
            bundle,
            args.bundle_build_manifest,
        )
        evidence = assemble_pilot_evidence(
            _load_run(args.run_2016),
            _load_run(args.run_2025),
            **bundle_evidence,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(evidence, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (OSError, ValueError) as exc:
        return _fail(str(exc))
    print(
        json.dumps(
            {
                "assembled": True,
                "accepted_releases": ["2016", "2025"],
                "bundle_sha256": evidence["bundle_sha256"],
                "repository_commit": evidence["repository_commit"],
                "bundle_build_manifest_sha256": evidence[
                    "bundle_build_manifest_sha256"
                ],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _load_run(value: str) -> Any:
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_RUN_BYTES:
        raise ValueError("Pilot run must be a file no larger than 256 KiB.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Pilot run must be valid UTF-8 JSON.") from exc


def _fail(message: str) -> int:
    print(json.dumps({"assembled": False, "error": message}, ensure_ascii=False, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
