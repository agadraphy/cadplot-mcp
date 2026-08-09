from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

MAX_SOURCE_FILE_BYTES = 10 * 1024 * 1024
MAX_CONTENT_SCAN_BYTES = 2 * 1024 * 1024
FORBIDDEN_SUFFIXES = {
    ".3dm",
    ".3ds",
    ".7z",
    ".bak",
    ".ctb",
    ".dwg",
    ".dwt",
    ".dxf",
    ".exe",
    ".max",
    ".p12",
    ".pc3",
    ".pdf",
    ".pem",
    ".pfx",
    ".pmp",
    ".rar",
    ".rfa",
    ".rvt",
    ".skp",
    ".stb",
    ".sv$",
    ".zip",
}
FORBIDDEN_NAMES = {
    ".env",
    "claude_desktop_config.json",
    "config.yaml",
    "credentials.json",
}
AUTODESK_ASSEMBLIES = {"accoremgd.dll", "acdbmgd.dll", "acmgd.dll"}
SECRET_PATTERNS = {
    "private key": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    "OpenAI API key": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}
WORKFLOW_PREFIX = ".github/workflows/"
PINNED_ACTION_PATTERN = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}$"
)
TRUSTED_ACTION_REPOSITORIES = {
    "actions/checkout",
    "actions/setup-dotnet",
    "actions/setup-python",
    "astral-sh/setup-uv",
}


def _git_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return sorted(
        (item.decode("utf-8") for item in result.stdout.split(b"\0") if item),
        key=str.casefold,
    )


def audit_paths(root: Path, relative_paths: list[str]) -> list[str]:
    resolved_root = root.expanduser().resolve(strict=True)
    violations: list[str] = []
    for relative in relative_paths:
        display = relative.replace("\\", "/")
        candidate = resolved_root / relative
        if candidate.is_symlink() or _is_reparse_point(candidate):
            violations.append(f"filesystem redirect: {display}")
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            violations.append(f"missing or unreadable source file: {display}")
            continue
        if resolved_root not in resolved.parents or not resolved.is_file():
            violations.append(f"source path escapes repository: {display}")
            continue

        leaf = resolved.name.casefold()
        suffix = resolved.suffix.casefold()
        if leaf in FORBIDDEN_NAMES:
            violations.append(f"local configuration or credential file: {display}")
        if leaf in AUTODESK_ASSEMBLIES:
            violations.append(f"Autodesk managed assembly: {display}")
        elif suffix in FORBIDDEN_SUFFIXES:
            violations.append(f"proprietary, secret, binary, or archive file: {display}")

        size = resolved.stat().st_size
        if size > MAX_SOURCE_FILE_BYTES:
            violations.append(f"oversized source file ({size} bytes): {display}")
            continue
        if size > MAX_CONTENT_SCAN_BYTES:
            continue
        try:
            content = resolved.read_bytes()
        except OSError:
            violations.append(f"unreadable source content: {display}")
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(content):
                violations.append(f"high-confidence {label}: {display}")
    return sorted(set(violations))


def _mapping_nodes(value: object) -> list[dict[object, object]]:
    nodes: list[dict[object, object]] = []
    if isinstance(value, dict):
        nodes.append(value)
        for child in value.values():
            nodes.extend(_mapping_nodes(child))
    elif isinstance(value, list):
        for child in value:
            nodes.extend(_mapping_nodes(child))
    return nodes


def audit_workflows(root: Path, relative_paths: list[str]) -> tuple[list[str], int, int]:
    workflow_paths = sorted(
        (
            relative
            for relative in relative_paths
            if relative.replace("\\", "/").startswith(WORKFLOW_PREFIX)
            and Path(relative).suffix.casefold() in {".yml", ".yaml"}
        ),
        key=str.casefold,
    )
    violations: list[str] = []
    action_references = 0
    if not workflow_paths:
        return ["GitHub Actions workflow set is empty"], 0, 0

    for relative in workflow_paths:
        display = relative.replace("\\", "/")
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
            document = yaml.safe_load(text)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            violations.append(f"invalid GitHub Actions workflow ({exc}): {display}")
            continue
        if not isinstance(document, dict):
            violations.append(f"GitHub Actions workflow is not a mapping: {display}")
            continue
        if document.get("permissions") != {"contents": "read"}:
            violations.append(
                f"GitHub Actions workflow permissions are not exact read-only: {display}"
            )
        if re.search(r"(?m)^\s*pull_request_target\s*:", text):
            violations.append(f"GitHub Actions workflow uses pull_request_target: {display}")

        for node in _mapping_nodes(document):
            reference = node.get("uses")
            if not isinstance(reference, str):
                continue
            reference = reference.strip()
            if reference.startswith("./"):
                continue
            action_references += 1
            if reference.startswith("docker://"):
                if not re.search(r"@sha256:[0-9a-f]{64}$", reference):
                    violations.append(
                        f"GitHub Actions container is not digest-pinned: {display}: {reference}"
                    )
                continue
            if not PINNED_ACTION_PATTERN.fullmatch(reference):
                violations.append(
                    "GitHub Actions reference is not pinned to a full commit: "
                    f"{display}: {reference}"
                )
                continue
            repository = reference.rsplit("@", 1)[0].casefold()
            if repository not in TRUSTED_ACTION_REPOSITORIES:
                violations.append(
                    f"GitHub Actions repository is not allowlisted: {display}: {repository}"
                )
                continue
            if reference.casefold().startswith("actions/checkout@"):
                options = node.get("with")
                persisted = (
                    options.get("persist-credentials") if isinstance(options, dict) else None
                )
                if persisted is not False and str(persisted).casefold() != "false":
                    violations.append(
                        f"GitHub checkout persists credentials: {display}: {reference}"
                    )
    return sorted(set(violations)), len(workflow_paths), action_references


def _is_reparse_point(path: Path) -> bool:
    try:
        return bool(path.lstat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def main() -> int:
    root_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if root_result.returncode != 0:
        print(json.dumps({"passed": False, "error": "Not inside a Git repository."}, indent=2))
        return 1
    root = Path(root_result.stdout.strip()).resolve(strict=True)
    try:
        paths = _git_paths(root)
        violations = audit_paths(root, paths)
        workflow_violations, workflow_count, action_count = audit_workflows(root, paths)
        violations.extend(workflow_violations)
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        return 1
    report = {
        "passed": not violations,
        "files_scanned": len(paths),
        "workflow_files_scanned": workflow_count,
        "workflow_action_references": action_count,
        "violations": sorted(set(violations)),
    }
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
