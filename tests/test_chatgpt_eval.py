from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cadplot_mcp.chatgpt_eval import (
    EVALUATION_CASES,
    EXTERNAL_GATES,
    OFFICIAL_GUIDE,
    WRITE_TOOLS,
    build_evaluation_plan,
    build_results_template,
    evaluation_plan_sha256,
    validate_evaluation_plan,
    validate_evaluation_results,
    validate_preflight_for_evaluation,
)


def _preflight(digest: str = "a" * 64) -> dict[str, object]:
    return {
        "schema_version": 1,
        "scope": "local-secure-tunnel-handoff",
        "local_handoff_ready": True,
        "target_probe_requested": True,
        "local_target_proven": True,
        "target_probe": {
            "passed": True,
            "tool_count": 20,
            "tool_surface_sha256": digest,
            "exact_tool_names": True,
            "instructions_contract": True,
            "annotations_contract": True,
            "closed_output_schemas": True,
            "secrets_included": False,
            "machine_paths_included": False,
            "autocad_launched": False,
            "live_tunnel_proven": False,
            "live_publish_proven": False,
        },
        "secrets_included": False,
        "machine_paths_included": False,
        "autocad_launched": False,
        "live_tunnel_proven": False,
        "live_publish_proven": False,
    }


def _completed_results(plan: dict[str, object]) -> dict[str, object]:
    results = build_results_template(plan)
    results["evaluated_utc"] = "2026-08-09T12:34:56Z"
    results["reviewer_role"] = "Authorized ChatGPT workspace evaluator"
    results["external_gates"] = {name: True for name in EXTERNAL_GATES}
    results["live_tunnel_proven"] = True
    for expected, actual in zip(plan["cases"], results["cases"], strict=True):
        actual["observed_tools"] = list(expected["required_tools_in_order"])
        actual["confirmation"] = expected["expected_confirmation"]
        actual["outcome"] = expected["expected_outcome"]
        actual["schema_valid"] = True
        actual["response_grounded"] = True
    return results


def test_plan_is_canonical_sanitized_and_covers_required_categories() -> None:
    plan = build_evaluation_plan("a" * 64)

    assert validate_evaluation_plan(plan) is plan
    assert plan["case_count"] == len(EVALUATION_CASES) == 13
    assert plan["official_guide"] == OFFICIAL_GUIDE
    assert plan["company_data_included"] is False
    assert plan["raw_chat_content_included"] is False
    assert plan["live_tunnel_proven"] is False
    assert plan["live_publish_proven"] is False
    assert {case["category"] for case in plan["cases"]} >= {
        "direct",
        "indirect",
        "follow_up",
        "approval",
        "adversarial",
        "edge",
        "destructive_confirmation",
    }
    assert {case["case_id"] for case in plan["cases"]} >= {
        "approved_queue_followup",
        "policy_bypass_attempt",
        "pending_cancel",
    }


def test_refusal_cases_forbid_every_write_tool() -> None:
    plan = build_evaluation_plan("a" * 64)
    cases = {case["case_id"]: case for case in plan["cases"]}

    for case_id in ("missing_plan_approval", "missing_manifest_approval", "policy_bypass_attempt"):
        assert set(cases[case_id]["forbidden_tools"]) == WRITE_TOOLS
        assert cases[case_id]["required_tools_in_order"] == []


def test_preflight_must_contain_exact_local_probe_without_live_claims() -> None:
    assert validate_preflight_for_evaluation(_preflight()) == "a" * 64

    for mutation in (
        lambda value: value.update(local_target_proven=False),
        lambda value: value["target_probe"].update(tool_count=19),
        lambda value: value["target_probe"].update(annotations_contract=False),
        lambda value: value.update(live_tunnel_proven=True),
        lambda value: value["target_probe"].update(machine_paths_included=True),
    ):
        value = _preflight()
        mutation(value)
        with pytest.raises(ValueError):
            validate_preflight_for_evaluation(value)


def test_completed_results_validate_without_claiming_publish_or_autocad_acceptance() -> None:
    plan = build_evaluation_plan("b" * 64)
    result = validate_evaluation_results(plan, _completed_results(plan))

    assert result["valid"] is True
    assert result["case_count"] == result["passed_case_count"] == 13
    assert result["live_tunnel_proven"] is True
    assert result["live_publish_proven"] is False
    assert result["licensed_autocad_acceptance_proven"] is False
    assert result["company_data_included"] is False
    assert result["raw_chat_content_included"] is False


def test_results_reject_plan_tamper_wrong_order_and_forbidden_write() -> None:
    plan = build_evaluation_plan("c" * 64)
    tampered_plan = copy.deepcopy(plan)
    tampered_plan["cases"][0]["prompt_template"] = "changed"
    with pytest.raises(ValueError, match="canonical"):
        validate_evaluation_plan(tampered_plan)

    results = _completed_results(plan)
    queue_case = next(
        case for case in results["cases"] if case["case_id"] == "approved_queue_followup"
    )
    queue_case["observed_tools"] = list(reversed(queue_case["observed_tools"]))
    with pytest.raises(ValueError, match="order"):
        validate_evaluation_results(plan, results)

    results = _completed_results(plan)
    refusal = next(
        case for case in results["cases"] if case["case_id"] == "policy_bypass_attempt"
    )
    refusal["observed_tools"] = ["queue_publish_job"]
    with pytest.raises(ValueError, match="Forbidden"):
        validate_evaluation_results(plan, results)

    results = _completed_results(plan)
    inventory = next(
        case for case in results["cases"] if case["case_id"] == "direct_inventory"
    )
    inventory["observed_tools"].append("scan_drawings")
    with pytest.raises(ValueError, match="Unexpected"):
        validate_evaluation_results(plan, results)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("evaluation_plan_sha256", "0" * 64, "exact plan"),
        ("tool_surface_sha256", "0" * 64, "different tool surface"),
        ("evaluated_utc", "2026-08-09", "RFC 3339"),
        ("reviewer_role", "x", "reviewer_role"),
        ("company_data_included", True, "company_data_included"),
        ("raw_chat_content_included", True, "raw_chat_content_included"),
        ("live_publish_proven", True, "live_publish_proven"),
        ("licensed_autocad_acceptance_proven", True, "licensed_autocad"),
        ("live_tunnel_proven", False, "live_tunnel_proven"),
    ),
)
def test_results_fail_closed_on_identity_privacy_or_claim_mutation(
    field: str, value: object, message: str
) -> None:
    plan = build_evaluation_plan("d" * 64)
    results = _completed_results(plan)
    results[field] = value

    with pytest.raises(ValueError, match=message):
        validate_evaluation_results(plan, results)


def test_prepare_cli_writes_once_and_validate_cli_accepts_completed_record(
    tmp_path: Path,
) -> None:
    preflight = tmp_path / "preflight.json"
    preflight.write_text(json.dumps(_preflight("e" * 64)), encoding="utf-8")
    output = tmp_path / "evaluation"
    command = [
        sys.executable,
        "-m",
        "cadplot_mcp.chatgpt_eval_cli",
        "prepare",
        "--preflight",
        str(preflight),
        "--output-dir",
        str(output),
    ]

    prepared = subprocess.run(command, capture_output=True, text=True, check=False)
    assert prepared.returncode == 0, prepared.stderr
    summary = json.loads(prepared.stdout)
    assert summary["prepared"] is True
    assert summary["case_count"] == 13
    assert summary["live_tunnel_proven"] is False
    assert summary["live_publish_proven"] is False
    assert summary["machine_paths_included"] is False
    assert str(output) not in prepared.stdout

    repeated = subprocess.run(command, capture_output=True, text=True, check=False)
    assert repeated.returncode == 1
    assert json.loads(repeated.stdout)["valid"] is False

    plan_path = output / "chatgpt-eval-plan.json"
    results_path = output / "chatgpt-eval-results.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    results_path.write_text(
        json.dumps(_completed_results(plan), ensure_ascii=False), encoding="utf-8"
    )
    validated = subprocess.run(
        [
            sys.executable,
            "-m",
            "cadplot_mcp.chatgpt_eval_cli",
            "validate",
            "--plan",
            str(plan_path),
            "--results",
            str(results_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    report = json.loads(validated.stdout)
    assert report["valid"] is True
    assert report["evaluation_plan_sha256"] == evaluation_plan_sha256(plan)
