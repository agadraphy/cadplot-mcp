import json
import subprocess
import sys
from pathlib import Path


def test_real_stdio_mcp_initialize_and_tool_contract() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "smoke-mcp-stdio.py"

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["server_name"] == "CadPlot MCP"
    assert report["tool_count"] == 18
    assert report["write_tools"] == [
        "queue_publish_batch",
        "queue_publish_job",
        "stage_publish_batch",
        "stage_publish_job",
    ]
