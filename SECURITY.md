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

## Supported versions

Security fixes are considered for the latest released version and the current
`main` branch. Alpha versions may change without compatibility guarantees.
