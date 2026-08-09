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
            "cadplot-tunnel-preflight",
            "cadplot-chatgpt-eval",
            "cadplot-sbom",
        )
        acceptance_commands = ("cadplot-acceptance",)
        for command_name in pilot_commands + acceptance_commands:
            command_path = command_root / f"{command_name}{command_suffix}"
            if not command_path.is_file():
                raise RuntimeError(f"Installed wheel is missing command: {command_name}")
            _run(
                [str(command_path), "--help"],
                cwd=temporary_root,
                environment=environment,
            )

        sbom_audit = temporary_root / "dependency-audit.json"
        sbom_source = temporary_root / "source.zip"
        sbom_output = temporary_root / "cadplot-mcp.cdx.json"
        sbom_source.write_bytes(b"installed-wheel-sbom-smoke")
        sbom_audit.write_text(
            json.dumps(
                {
                    "generated_utc": "2026-08-09T00:00:00+00:00",
                    "passed": True,
                    "lock": {
                        "file": "uv.lock",
                        "sha256": "a" * 64,
                        "requirements_sha256": "b" * 64,
                    },
                    "python": {"package_count": 1},
                    "python_license_inventory": {
                        "package_count": 1,
                        "unknown_count": 0,
                        "packages": [
                            {"name": "fixture", "version": "1.0", "license": "MIT"}
                        ],
                    },
                    "autocad_launched": False,
                    "live_publish_proven": False,
                }
            ),
            encoding="utf-8",
        )
        sbom_command = command_root / f"cadplot-sbom{command_suffix}"
        sbom_generate = json.loads(
            _run(
                [
                    str(sbom_command),
                    "generate",
                    "--dependency-audit",
                    str(sbom_audit),
                    "--commit",
                    "0" * 40,
                    "--version",
                    package["version"],
                    "--artifact",
                    f"wheel={wheel}",
                    "--artifact",
                    f"source-archive={sbom_source}",
                    "--output",
                    str(sbom_output),
                ],
                cwd=temporary_root,
                environment=environment,
            )
        )
        sbom_validate = json.loads(
            _run(
                [
                    str(sbom_command),
                    "validate",
                    str(sbom_output),
                    "--commit",
                    "0" * 40,
                    "--version",
                    package["version"],
                ],
                cwd=temporary_root,
                environment=environment,
            )
        )
        if (
            sbom_generate.get("passed") is not True
            or sbom_generate.get("runtime_dependency_count") != 1
            or sbom_generate.get("artifact_count") != 2
            or sbom_generate.get("machine_paths_included") is not False
            or sbom_validate.get("passed") is not True
            or sbom_validate.get("component_count") != 3
            or sbom_validate.get("autocad_launched") is not False
            or sbom_validate.get("live_publish_proven") is not False
        ):
            raise RuntimeError("Installed wheel failed the CycloneDX SBOM CLI smoke.")

        tunnel_input = temporary_root / "tunnel-input"
        tunnel_input.mkdir()
        tunnel_config = temporary_root / "tunnel-config.yaml"
        tunnel_config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_roots": [str(tunnel_input)],
                    "workspace_root": str(temporary_root / "tunnel-work"),
                    "paper_profiles": [
                        {
                            "id": "smoke_a4",
                            "labels": ["A4"],
                            "page_setup": "SMOKE_A4",
                            "plotter": "Smoke PDF.pc3",
                            "plot_style": "smoke.ctb",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        tunnel_environment = environment.copy()
        tunnel_environment["CADPLOT_CONFIG"] = str(tunnel_config)
        tunnel_environment["CADPLOT_TUNNEL_ID"] = (
            "tunnel_0123456789abcdef0123456789abcdef"
        )
        tunnel_environment["CONTROL_PLANE_API_KEY"] = "runtime-secret-sentinel"
        tunnel_environment["PATH"] = (
            str(command_root) + os.pathsep + tunnel_environment.get("PATH", "")
        )
        preflight_path = temporary_root / "tunnel-preflight.json"
        tunnel_output = _run(
            [
                str(command_root / f"cadplot-tunnel-preflight{command_suffix}"),
                "--transport",
                "stdio",
                "--probe-target",
                "--output",
                str(preflight_path),
            ],
            cwd=temporary_root,
            environment=tunnel_environment,
        )
        tunnel_report = json.loads(tunnel_output)
        if (
            tunnel_report.get("local_handoff_ready") is not True
            or tunnel_report.get("secrets_included") is not False
            or tunnel_report.get("machine_paths_included") is not False
            or tunnel_report.get("live_tunnel_proven") is not False
            or tunnel_report.get("target_probe_requested") is not True
            or tunnel_report.get("local_target_proven") is not True
            or tunnel_report.get("target_probe", {}).get("passed") is not True
            or tunnel_report.get("target_probe", {}).get("tool_count") != 20
            or tunnel_report.get("target_probe", {}).get("exact_tool_names") is not True
            or tunnel_report.get("target_probe", {}).get("autocad_launched") is not False
            or tunnel_report.get("autocad_launched") is not False
            or tunnel_report.get("live_publish_proven") is not False
            or "runtime-secret-sentinel" in tunnel_output
            or str(tunnel_config) in tunnel_output
            or not preflight_path.is_file()
            or json.loads(preflight_path.read_text(encoding="utf-8")) != tunnel_report
        ):
            raise RuntimeError("Installed wheel failed the secret-free tunnel preflight smoke.")

        evaluation_root = temporary_root / "chatgpt-evaluation"
        evaluation_output = _run(
            [
                str(command_root / f"cadplot-chatgpt-eval{command_suffix}"),
                "prepare",
                "--preflight",
                str(preflight_path),
                "--output-dir",
                str(evaluation_root),
            ],
            cwd=temporary_root,
            environment=tunnel_environment,
        )
        evaluation_report = json.loads(evaluation_output)
        if (
            evaluation_report.get("prepared") is not True
            or evaluation_report.get("case_count") != 13
            or evaluation_report.get("tool_surface_sha256")
            != tunnel_report["target_probe"]["tool_surface_sha256"]
            or evaluation_report.get("company_data_included") is not False
            or evaluation_report.get("raw_chat_content_included") is not False
            or evaluation_report.get("machine_paths_included") is not False
            or evaluation_report.get("autocad_launched") is not False
            or evaluation_report.get("live_tunnel_proven") is not False
            or evaluation_report.get("live_publish_proven") is not False
            or not (evaluation_root / "chatgpt-eval-plan.json").is_file()
            or not (evaluation_root / "chatgpt-eval-results.template.json").is_file()
        ):
            raise RuntimeError("Installed wheel failed the ChatGPT evaluation preparation smoke.")

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
        "acceptance_cli_commands": len(acceptance_commands),
        "sbom_cli_verified": True,
        "tunnel_preflight_redacted": True,
        "tunnel_preflight_target_probed": True,
        "tunnel_preflight_tool_surface_sha256": tunnel_report["target_probe"][
            "tool_surface_sha256"
        ],
        "chatgpt_eval_plan_prepared": True,
        "chatgpt_eval_case_count": evaluation_report["case_count"],
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
