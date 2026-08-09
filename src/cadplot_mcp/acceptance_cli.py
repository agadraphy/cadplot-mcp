from __future__ import annotations

import argparse
import json

from cadplot_mcp.acceptance import (
    build_release_acceptance,
    load_and_validate_release_acceptance,
    require_new_acceptance_output,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Finalize or revalidate the release kit plus two-version live acceptance."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    finalize = subparsers.add_parser("finalize", help="Create a new sanitized acceptance report.")
    _add_artifact_arguments(finalize)
    finalize.add_argument("--output", required=True)
    finalize.add_argument("--company-publication-approved", action="store_true")
    finalize.add_argument("--maintainer-release-approved", action="store_true")

    validate = subparsers.add_parser("validate", help="Revalidate an existing acceptance report.")
    _add_artifact_arguments(validate)
    validate.add_argument("--acceptance", required=True)
    args = parser.parse_args()

    try:
        if args.command == "finalize":
            output = require_new_acceptance_output(args.output)
            report = build_release_acceptance(
                args.release_root,
                args.pilot_evidence,
                company_publication_approved=args.company_publication_approved,
                maintainer_release_approved=args.maintainer_release_approved,
            )
            with output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        else:
            report = load_and_validate_release_acceptance(
                args.acceptance,
                release_root_value=args.release_root,
                pilot_evidence_value=args.pilot_evidence,
            )
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(
        json.dumps(
            {
                "valid": True,
                "exact_commit": report["exact_commit"],
                "package_version": report["package_version"],
                "accepted_releases": report["accepted_releases"],
                "licensed_live_pilot_ready": report["licensed_live_pilot_ready"],
                "live_publish_proven": report["live_publish_proven"],
                "public_release_ready": report["public_release_ready"],
                **({"output": str(output)} if args.command == "finalize" else {}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _add_artifact_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--release-root", required=True, help="Verified release-kit top-level root."
    )
    parser.add_argument("--pilot-evidence", required=True, help="Validated two-version pilot JSON.")


if __name__ == "__main__":
    raise SystemExit(main())
