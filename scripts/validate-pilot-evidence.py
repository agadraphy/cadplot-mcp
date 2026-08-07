from __future__ import annotations

import argparse
import json

from cadplot_mcp.pilot import load_and_validate_pilot_evidence


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate separate licensed AutoCAD 2016 and 2025 pilot evidence."
    )
    parser.add_argument("evidence", help="Path to the completed local pilot-evidence JSON file.")
    args = parser.parse_args()
    try:
        result = load_and_validate_pilot_evidence(args.evidence)
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
