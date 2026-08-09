import importlib.util
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "audit-source-tree.py"
SPEC = importlib.util.spec_from_file_location("cadplot_source_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_source_audit_accepts_small_public_text_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("public documentation", encoding="utf-8")
    (tmp_path / "example.yaml").write_text("version: 1", encoding="utf-8")

    assert MODULE.audit_paths(tmp_path, ["README.md", "example.yaml"]) == []


def test_source_audit_rejects_cad_assets_archives_and_local_config(tmp_path: Path) -> None:
    names = ["office.ctb", "drawing.dwg", "handoff.zip", "config.yaml"]
    for name in names:
        (tmp_path / name).write_bytes(b"private")

    violations = MODULE.audit_paths(tmp_path, names)

    assert any("office.ctb" in item for item in violations)
    assert any("drawing.dwg" in item for item in violations)
    assert any("handoff.zip" in item for item in violations)
    assert any("config.yaml" in item for item in violations)


def test_source_audit_rejects_high_confidence_secret_patterns(tmp_path: Path) -> None:
    secret = tmp_path / "notes.txt"
    fake_token = "ghp_" + "1234567890abcdefghijklmnop"
    secret.write_text(f"token={fake_token}", encoding="utf-8")

    violations = MODULE.audit_paths(tmp_path, [secret.name])

    assert violations == ["high-confidence GitHub token: notes.txt"]


def test_workflow_audit_accepts_read_only_full_sha_pins(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "tests.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """name: tests
permissions:
  contents: read
on:
  pull_request:
jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@1111111111111111111111111111111111111111
        with:
          persist-credentials: false
""",
        encoding="utf-8",
    )

    violations, workflow_count, action_count = MODULE.audit_workflows(
        tmp_path, [str(workflow.relative_to(tmp_path))]
    )

    assert violations == []
    assert workflow_count == 1
    assert action_count == 1


def test_workflow_audit_rejects_mutable_action_write_token_and_target_event(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "unsafe.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """name: unsafe
permissions: write-all
on:
  pull_request_target:
jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
""",
        encoding="utf-8",
    )

    violations, workflow_count, action_count = MODULE.audit_workflows(
        tmp_path, [str(workflow.relative_to(tmp_path))]
    )

    assert workflow_count == 1
    assert action_count == 1
    assert any("permissions are not exact read-only" in item for item in violations)
    assert any("uses pull_request_target" in item for item in violations)
    assert any("not pinned to a full commit" in item for item in violations)
