from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cadplot_mcp.demo import run_synthetic_demo


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cadplot-demo-") as value:
        result = run_synthetic_demo(Path(value))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
