"""CLI bindings for the opt-in, scoped public translation tools."""

import json
from pathlib import Path

from cadplot_protocol.translation_protocol import RemoteDecision
from pydantic import TypeAdapter

from cadplot_mcp.cli_auth import CLIError
from cadplot_mcp.translation_cli import _unique_object, approval_id


def register_commands(commands, opaque_id, bounded_int):
    root = commands.add_parser(
        "translate-remote", help="approved cloud text sharing via enrolled worker"
    )
    actions = root.add_subparsers(dest="translation_action", required=True)
    for action in ("scan", "submit", "plan", "apply"):
        command = actions.add_parser(action)
        command.add_argument("drawing_id", type=opaque_id("drw"))
        command.add_argument(
            "--inventory", dest="inventory_id", type=approval_id, required=action != "scan"
        )
        fields = ["drawing_id", "inventory_id"]
        if action in ("scan", "plan"):
            command.add_argument("--offset", type=int, default=0)
            command.add_argument("--limit", type=bounded_int(1, 100), default=25)
            fields += ["offset", "limit"]
        if action == "submit":
            command.add_argument(
                "--decisions",
                dest="decisions_file",
                required=True,
                help="UTF-8 JSON array of 1-100 exact span decisions",
            )
            command.add_argument("--previous-review", dest="expected_review_id", type=approval_id)
            fields += ["decisions", "expected_review_id"]
        if action in ("plan", "apply"):
            command.add_argument("--review-id", dest="review_id", type=approval_id, required=True)
            fields += ["review_id"]
        if action == "apply":
            command.add_argument(
                "--approve", dest="approved_plan_id", type=approval_id, required=True
            )
            command.add_argument(
                "--plan-operation", dest="plan_operation_id", type=opaque_id("op"), required=True
            )
            fields += ["approved_plan_id", "plan_operation_id"]
        tools = {
            "scan": "scan_drawing_texts",
            "submit": "submit_drawing_translations",
            "plan": "create_drawing_translation_plan",
            "apply": "apply_drawing_translation",
        }
        command.set_defaults(tool=tools[action], fields=tuple(fields))


def prepare_arguments(args):
    if getattr(args, "offset", 0) < 0:
        raise CLIError("invalid_translation_offset", "Offset must not be negative.", 2)
    if args.translation_action != "submit":
        return
    try:
        with Path(args.decisions_file).open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("batch too large")
        items = TypeAdapter(list[RemoteDecision]).validate_python(
            json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
        )
        if not 1 <= len(items) <= 100 or len({i.span_id for i in items}) != len(items):
            raise ValueError("invalid batch")
        args.decisions = [item.model_dump() for item in items]
    except (OSError, ValueError, TypeError, RecursionError):
        raise CLIError(
            "invalid_translation_batch",
            "Use a UTF-8 JSON array of 1-100 unique decisions, at most 128 KiB.",
            2,
        ) from None
