from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    input_text: str | None = None,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        details = (result.stdout + result.stderr).strip()
        raise RuntimeError(f"Command failed with exit {result.returncode}: {details}")
    return result.stdout.strip()


def _select_wheel(value: Path) -> Path:
    resolved = value.expanduser().resolve(strict=True)
    if resolved.is_file():
        if resolved.suffix.casefold() != ".whl":
            raise ValueError("Wheel smoke input must be a .whl file or a directory.")
        return resolved
    if not resolved.is_dir():
        raise ValueError("Wheel smoke input must be a file or directory.")
    wheels = sorted(resolved.glob("cadplot_mcp-*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"Expected exactly one cadplot_mcp wheel; found {len(wheels)}.")
    return wheels[0].resolve(strict=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def smoke_wheel(wheel_value: Path) -> dict[str, Any]:
    wheel = _select_wheel(wheel_value)
    repository_root = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for the isolated wheel smoke test.")
    smoke_script = Path(__file__).resolve().with_name("smoke-mcp-stdio.py")
    if not smoke_script.is_file():
        raise RuntimeError("The real MCP STDIO smoke script is missing.")
    http_smoke_script = Path(__file__).resolve().with_name("smoke-mcp-http.py")
    if not http_smoke_script.is_file():
        raise RuntimeError("The real MCP Streamable HTTP smoke script is missing.")

    environment = os.environ.copy()
    environment.pop("CADPLOT_CONFIG", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"

    with tempfile.TemporaryDirectory(prefix="cadplot-wheel-smoke-") as temporary:
        temporary_root = Path(temporary).resolve(strict=True)
        venv_root = temporary_root / "venv"
        requirements = temporary_root / "requirements.locked.txt"
        _run(
            [
                uv,
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--no-header",
                "--output-file",
                str(requirements),
            ],
            cwd=repository_root,
            environment=environment,
        )
        _run(
            [uv, "venv", "--python", sys.executable, str(venv_root)],
            cwd=temporary_root,
            environment=environment,
        )
        interpreter = venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        _run(
            [
                uv,
                "pip",
                "install",
                "--require-hashes",
                "--python",
                str(interpreter),
                "--requirements",
                str(requirements),
            ],
            cwd=temporary_root,
            environment=environment,
        )
        _run(
            [
                uv,
                "pip",
                "install",
                "--no-deps",
                "--python",
                str(interpreter),
                str(wheel),
            ],
            cwd=temporary_root,
            environment=environment,
        )
        package_output = _run(
            [
                str(interpreter),
                "-c",
                (
                    "import json, cadplot_mcp; "
                    "print(json.dumps({'version': cadplot_mcp.__version__, "
                    "'module': cadplot_mcp.__file__}))"
                ),
            ],
            cwd=temporary_root,
            environment=environment,
        )
        package = json.loads(package_output)
        module_path = Path(package["module"]).resolve(strict=True)
        if venv_root not in module_path.parents:
            raise RuntimeError("Wheel smoke imported cadplot_mcp outside the isolated environment.")

        protocol_output = _run(
            [str(interpreter), str(smoke_script)],
            cwd=temporary_root,
            environment=environment,
        )
        protocol = json.loads(protocol_output)
        if protocol.get("passed") is not True:
            raise RuntimeError("Installed wheel failed the real MCP STDIO smoke test.")

        http_protocol_output = _run(
            [str(interpreter), str(http_smoke_script)],
            cwd=temporary_root,
            environment=environment,
        )
        http_protocol = json.loads(http_protocol_output)
        if (
            http_protocol.get("passed") is not True
            or http_protocol.get("host") != "127.0.0.1"
            or http_protocol.get("invalid_host_blocked") is not True
            or http_protocol.get("invalid_origin_blocked") is not True
            or http_protocol.get("oversized_request_blocked") is not True
            or not isinstance(http_protocol.get("cleanup_retries"), int)
            or http_protocol["cleanup_retries"] < 0
        ):
            raise RuntimeError("Installed wheel failed the loopback HTTP MCP smoke test.")

        inspector_output = _run(
            [str(interpreter), "-m", "cadplot_mcp.inspector_worker"],
            cwd=temporary_root,
            environment=environment,
            input_text="{}",
        )
        inspector_protocol = json.loads(inspector_output)
        if inspector_protocol != {
            "ok": False,
            "error": "Inspection request schema is invalid.",
        }:
            raise RuntimeError("Installed wheel failed the isolated inspector protocol smoke.")

        command_root = venv_root / ("Scripts" if os.name == "nt" else "bin")
        command_suffix = ".exe" if os.name == "nt" else ""
        pilot_commands = (
            "cadplot-mcp-http",
            "cadplot-collect-pilot",
            "cadplot-assemble-pilot",
            "cadplot-validate-pilot",
        )
        for command_name in pilot_commands:
            command_path = command_root / f"{command_name}{command_suffix}"
            if not command_path.is_file():
                raise RuntimeError(f"Installed wheel is missing command: {command_name}")
            _run(
                [str(command_path), "--help"],
                cwd=temporary_root,
                environment=environment,
            )

    return {
        "passed": True,
        "wheel": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "version": package["version"],
        "source_tree_imported": False,
        "protocol_version": protocol["protocol_version"],
        "tool_count": protocol["tool_count"],
        "closed_output_schemas": protocol["closed_output_schemas"],
        "structured_output_calls": protocol["structured_output_calls"],
        "http_transport_protocol_version": http_protocol["protocol_version"],
        "http_transport_tool_count": http_protocol["tool_count"],
        "http_transport_loopback_only": True,
        "http_transport_header_guards": True,
        "http_transport_cleanup_retries": http_protocol["cleanup_retries"],
        "pilot_cli_commands": len(pilot_commands),
        "inspector_worker_protocol": True,
        "isolated_install": True,
        "locked_dependencies": True,
        "dependency_hashes_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install the built wheel into a temporary venv and smoke its real MCP server."
        )
    )
    parser.add_argument("wheel", nargs="?", default="dist", type=Path)
    args = parser.parse_args()
    try:
        report = smoke_wheel(args.wheel)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
