# CadPlot CLI

`cadplot` is a Python 3.11+ command-line client for the public OAuth-protected MCP.
It uses the same seven tools and account/workstation permissions as an MCP chat client.
No AI model subscription is needed to run CLI commands. The client does not install
AutoCAD, enroll a workstation, modify a drawing, or publish PDFs.

## Install from GitHub

With Python and Git installed, use an isolated environment:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install "git+https://github.com/agadraphy/cadplot-mcp.git@main"
.venv\Scripts\cadplot --help
.venv\Scripts\cadplot health
.venv\Scripts\cadplot login
.venv\Scripts\cadplot workstations list
```

Alternatively, if you already use pipx:

```sh
pipx install "git+https://github.com/agadraphy/cadplot-mcp.git@main"
cadplot login
cadplot workstations list
```

On macOS/Linux, the virtual-environment executable is `.venv/bin/cadplot`.
These are GitHub installs, not a claim that this project is published on PyPI.
Replace `main` with a reviewed full commit SHA for reproducible source selection.
Existing local entry points (`cadplot-mcp`, `cadplot-doctor`, `cadplot-worker`, etc.)
remain available and unchanged. See the [Windows worker guide](windows-worker.md)
to enroll a licensed AutoCAD workstation; CLI login alone does not do that.

## Sign-in and credential storage

The default server is `https://cadplot-mcp-gateway.onrender.com/mcp`.
`cadplot login` discovers its OAuth settings, dynamically registers a public client,
and opens the identity provider in your browser. It uses PKCE S256 and checks the
callback state. Only the server-advertised CadPlot scope is requested.

The callback listens on `127.0.0.1:43817` while signing in, never on a public interface.
If busy, use `cadplot login --port 43818`. If a client is already saved for a different
port, first run `cadplot logout`. Login allows five minutes for browser interaction
in addition to the request budget. No password is entered into the CLI.

Credentials are stored per server in Windows Credential Manager (local machine
persistence), macOS Keychain, or Linux Secret Service. The CLI selects these native
backends explicitly; it will not use a configured plaintext third-party keyring.
Linux requires a running, unlocked Secret Service and a desktop D-Bus session.
Headless systems without a secure credential store are not supported for login.
Native store size/access limits fail closed, rather than falling back to a file.

Saved token expiry is preserved across CLI processes. Refresh tokens are used when
the provider grants them; otherwise sign in again when prompted. OAuth discovery
metadata is saved to ensure refresh requests go to the discovered identity provider,
not a guessed endpoint on the MCP server. All remote HTTP requests require HTTPS.

`cadplot logout` removes this endpoint's local tokens, registration and discovery
records. It does **not** revoke provider sessions/tokens or delete the remote DCR
client. Use the identity provider's account controls for server-side revocation.

## Commands

Global options (`--server`, `--timeout`, `--json`) go **before** the command.
`CADPLOT_SERVER` can override the default; credentials are isolated by normalized
server URL, including path. Keep the exact endpoint path consistent.

```sh
cadplot health
cadplot tools
cadplot workstations list
cadplot workstations validate ws_<uuid>
cadplot projects list --workstation ws_<uuid>
cadplot drawings scan --project prj_<uuid> --limit 100
cadplot drawings scan --project prj_<uuid> --no-recursive
cadplot drawings inspect drw_<uuid>
cadplot plan drw_<uuid> --dry-run
cadplot operations status op_<uuid>
cadplot --json operations status op_<uuid>
cadplot --timeout 180 health
```

The `<uuid>` entries above are placeholders, not literal commands: copy the complete
opaque IDs returned by preceding operations. Paths and arbitrary CAD commands are
not accepted. Scans are recursive by default and limited to 1–100 drawings.
`plan` is always a dry run, even without the optional `--dry-run` marker.

`workstations list` returns the account's workstation list. The five other discovery,
inspection and planning commands enqueue operations. Save their `operation_id` and
check it with `operations status` until a terminal state is returned. `CREATED`,
`LEASED`, or `RUNNING` means **not yet finished**; `SUCCEEDED` carries the result.
No automatic enqueue retry or hidden polling is performed. After a network failure,
the enqueue outcome may be unknown: do not blindly resubmit it. A local timeout or
Ctrl+C does not cancel an already accepted remote operation.

Without an enrolled, online worker and approved projects, signing in can succeed
while drawing commands remain unavailable. Empty workstation lists are not an
AutoCAD connection test. This CLI does not add cloud publish/write capabilities.

## Output and exit codes

Successful responses are JSON on stdout (indented normally, compact with `--json`).
Operational errors are a JSON `error` object on stderr with a stable `code` and safe
message; raw OAuth/provider exceptions are not printed. Browser sign-in guidance
also goes to stderr. Argument errors use argparse's normal usage text on stderr.

| Code | Meaning |
| --- | --- |
| 0 | Successful request; queued work may still be pending |
| 1 | Request/tool failure, or an operation in FAILED/EXPIRED/ATTENTION_REQUIRED |
| 2 | Invalid command or arguments |
| 3 | Login or authorization required/failed |
| 4 | Native credential store unavailable, invalid or inaccessible |
| 5 | Timeout; remote work may still be running |
| 130 | Local keyboard cancellation |

Render's free-tier cold start can delay the first request; the default request/command
budget is 120 seconds and can be set between 1 and 600 seconds. `health` only checks
gateway readiness, not OAuth completion, worker availability or real DWG acceptance.

## Development checks

`tests/test_cli.py` exercises all seven command mappings using the real Python MCP
SDK over mocked HTTPS, including DCR, PKCE, saved credentials, refresh on a subsequent
CLI process, loopback callback rejection, safe errors and operation exit codes.
These checks are not a licensed AutoCAD or production OAuth end-to-end acceptance test.
The SDK persistence adapter deliberately targets the pinned MCP 1.29 series; rerun
these tests before changing that dependency.
