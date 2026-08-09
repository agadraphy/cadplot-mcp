from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from cadplot_mcp.batch_demo import canonical_batch_demo_digest, run_synthetic_batch_demo


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rehearse bounded CadPlot batch handling without AutoCAD or real DWGs."
    )
    parser.add_argument("--drawings", type=int, default=300)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional new JSON evidence path; existing files are never overwritten.",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cadplot-batch-demo-") as value:
        result = run_synthetic_batch_demo(Path(value), drawing_count=args.drawings)
        result["evidence_digest"] = canonical_batch_demo_digest(result)
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output is not None:
            output = args.output.expanduser().resolve(strict=False)
            if not output.parent.is_dir():
                raise ValueError("Batch evidence output parent must already exist.")
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
        print(payload, end="")


if __name__ == "__main__":
    main()
