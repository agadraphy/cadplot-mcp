from __future__ import annotations

import argparse
import json
from pathlib import Path

from cadplot_mcp.chatgpt_eval import (
    assert_no_redirected_ancestor,
    build_evaluation_plan,
    build_results_template,
    evaluation_plan_sha256,
    load_json_file,
    validate_evaluation_plan,
    validate_evaluation_results,
    validate_preflight_for_evaluation,
    write_new_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or validate sanitized ChatGPT tool-selection evaluation evidence. "
            "This never starts a tunnel, AutoCAD, or a publish job."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare", help="Create a canonical plan and editable result template without overwrite."
    )
    prepare.add_argument("--preflight", required=True)
    prepare.add_argument("--output-dir", required=True)
    validate = subparsers.add_parser(
        "validate", help="Validate a completed, sanitized external ChatGPT evaluation record."
    )
    validate.add_argument("--plan", required=True)
    validate.add_argument("--results", required=True)
    args = parser.parse_args()

    try:
        if args.command == "prepare":
            return _prepare(args.preflight, args.output_dir)
        return _validate(args.plan, args.results)
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


def _prepare(preflight_value: str, output_dir_value: str) -> int:
    preflight = load_json_file(preflight_value, label="Tunnel preflight")
    tool_surface_sha256 = validate_preflight_for_evaluation(preflight)
    plan = build_evaluation_plan(tool_surface_sha256)
    output_candidate = Path(output_dir_value).expanduser()
    assert_no_redirected_ancestor(output_candidate)
    output_dir = output_candidate.resolve(strict=False)
    if output_dir.exists():
        raise ValueError(
            "Evaluation output directory already exists; preparation never overwrites."
        )
    try:
        output_dir.mkdir(parents=True)
    except OSError as exc:
        raise ValueError("Evaluation output directory could not be created safely.") from exc
    plan_path = write_new_json(output_dir / "chatgpt-eval-plan.json", plan)
    template_path = write_new_json(
        output_dir / "chatgpt-eval-results.template.json",
        build_results_template(plan),
    )
    print(
        json.dumps(
            {
                "prepared": True,
                "case_count": plan["case_count"],
                "evaluation_plan_sha256": evaluation_plan_sha256(plan),
                "tool_surface_sha256": tool_surface_sha256,
                "plan": plan_path.name,
                "results_template": template_path.name,
                "company_data_included": False,
                "raw_chat_content_included": False,
                "machine_paths_included": False,
                "autocad_launched": False,
                "live_tunnel_proven": False,
                "live_publish_proven": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _validate(plan_value: str, results_value: str) -> int:
    plan = validate_evaluation_plan(load_json_file(plan_value, label="Evaluation plan"))
    result = validate_evaluation_results(
        plan,
        load_json_file(results_value, label="Evaluation results"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
