from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOL_COUNT = 18


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_listening(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"HTTP MCP server exited during startup: {process.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("HTTP MCP server did not bind its loopback port within 10 seconds")


async def _exercise(url: str, input_root: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5) as client:
        invalid_host = await client.post(
            url,
            headers={"Host": "attacker.invalid"},
            json={},
        )
        if invalid_host.status_code != 421:
            raise RuntimeError(
                f"Unexpected invalid-Host status: {invalid_host.status_code}"
            )
        invalid_origin = await client.post(
            url,
            headers={"Origin": "https://attacker.invalid"},
            json={},
        )
        if invalid_origin.status_code != 403:
            raise RuntimeError(
                f"Unexpected invalid-Origin status: {invalid_origin.status_code}"
            )
        oversized = await client.post(
            url,
            content=b'{"padding":"' + (b"x" * (1024 * 1024)) + b'"}',
            headers={"Content-Type": "application/json"},
        )
        if oversized.status_code != 413:
            raise RuntimeError(
                f"Unexpected oversized-request status: {oversized.status_code}"
            )

    async with streamable_http_client(url) as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            match_result = await session.call_tool(
                "match_paper_profile", {"label": "1000 x 700 mm"}
            )
            scan_result = await session.call_tool(
                "scan_drawings", {"root": input_root, "recursive": True}
            )

    if initialized.serverInfo.name != "CadPlot MCP":
        raise RuntimeError("Loopback endpoint initialized the wrong MCP server")
    if len(listed.tools) != EXPECTED_TOOL_COUNT:
        raise RuntimeError("Loopback endpoint exposed an unexpected tool count")
    if match_result.isError or match_result.structuredContent.get("matched") is not True:
        raise RuntimeError("Loopback structured profile call failed")
    if scan_result.isError or scan_result.structuredContent.get("count") != 0:
        raise RuntimeError("Loopback structured scan call failed")
    return {
        "protocol_version": initialized.protocolVersion,
        "server_name": initialized.serverInfo.name,
        "tool_count": len(listed.tools),
        "structured_output_calls": 2,
        "invalid_host_blocked": True,
        "invalid_origin_blocked": True,
        "oversized_request_blocked": True,
    }


def smoke() -> dict[str, Any]:
    port = _reserve_port()
    with tempfile.TemporaryDirectory(prefix="cadplot-http-smoke-") as temporary:
        root = Path(temporary)
        input_root = root / "input"
        workspace_root = root / "work"
        input_root.mkdir()
        workspace_root.mkdir()
        config_path = root / "config.yaml"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_roots": [str(input_root)],
                    "workspace_root": str(workspace_root),
                    "paper_profiles": [
                        {
                            "id": "smoke_70x100",
                            "labels": ["1000 x 700 mm"],
                            "page_setup": "SMOKE_70X100",
                            "plotter": "Smoke PDF.pc3",
                            "plot_style": "smoke.ctb",
                            "tolerance_mm": 2,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["CADPLOT_CONFIG"] = str(config_path)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as server_log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cadplot_mcp.http_server",
                    "--port",
                    str(port),
                ],
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=server_log,
                stderr=server_log,
                text=True,
            )
            try:
                _wait_until_listening(process, port)
                report = asyncio.run(
                    asyncio.wait_for(
                        _exercise(f"http://127.0.0.1:{port}/mcp", str(input_root)),
                        timeout=20,
                    )
                )
            except Exception as exc:
                server_log.seek(0)
                details = server_log.read().strip()
                raise RuntimeError(f"{exc}; server log: {details[-4000:]}") from exc
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)

    return {
        "passed": True,
        **report,
        "host": "127.0.0.1",
        "path": "/mcp",
        "request_limit_bytes": 1024 * 1024,
        "autocad_launched": False,
        "live_publish_proven": False,
    }


def main() -> int:
    try:
        report = smoke()
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
