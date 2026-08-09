# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately through the repository's
GitHub security advisory workflow. If that is unavailable, open a minimal
issue requesting a private reporting channel; do not include exploit details
or sensitive drawing data in the issue.

Include affected version, impact, reproduction steps, and a safe proof of
concept where possible. We will acknowledge reports, investigate them, and
coordinate a fix before public disclosure. Please allow a reasonable time for
remediation and do not publish a vulnerability before coordination.

## Safety boundary

`cadplot-mcp` is designed to inspect drawings and plan plot jobs safely. It
must not be relied on as the sole control for production plotting, approvals,
or preservation of original CAD data. Review every proposed action in the
target AutoCAD environment.

Default workflows must remain copy-only and dry-run first. Do not overwrite,
move, delete, batch-modify, or submit drawings without an explicit operator
decision and a verified output location.

Run `uv run python scripts/audit-source-tree.py` before committing or publishing.
The same check runs in CI and local preflight to reject tracked or stageable CAD/plot
assets, archives, local configuration, Autodesk assemblies, oversized files, and
high-confidence credential patterns. This is a guardrail, not a substitute for review.

The audit also requires every external GitHub Action to use an immutable full commit SHA,
read-only workflow permissions, checkout credential persistence disabled, and no
`pull_request_target` trigger. `.github/dependabot.yml` schedules updates for the exact `uv` lock,
NuGet projects, and pinned Actions. Repository administrators must separately enable Dependabot
alerts/security updates and branch protection in GitHub settings after the repository is created.

The local named pipe is restricted to the creating Windows user. .NET 8 uses
`PipeOptions.CurrentUserOnly`; the AutoCAD 2016/.NET Framework 4.5 build uses
a protected ACL granting full control only to the current user SID. Do not
relay the pipe over TCP or expose it through a generic command bridge.

## Supported versions

Security fixes are considered for the latest released version and the current
`main` branch. Alpha versions may change without compatibility guarantees.
