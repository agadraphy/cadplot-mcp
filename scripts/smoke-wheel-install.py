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


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
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
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for the isolated wheel smoke test.")
    smoke_script = Path(__file__).resolve().with_name("smoke-mcp-stdio.py")
    if not smoke_script.is_file():
        raise RuntimeError("The real MCP STDIO smoke script is missing.")

    environment = os.environ.copy()
    environment.pop("CADPLOT_CONFIG", None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"

    with tempfile.TemporaryDirectory(prefix="cadplot-wheel-smoke-") as temporary:
        temporary_root = Path(temporary).resolve(strict=True)
        venv_root = temporary_root / "venv"
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
        "isolated_install": True,
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
