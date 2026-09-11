from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from cadplot_mcp import cli, cli_auth
from cadplot_mcp.cli_auth import CLIError, CredentialStore, LoopbackLogin

SERVER = "https://gateway.example/mcp"
ISSUER = "https://identity.example"
UUID = "12345678-1234-4234-8234-123456789abc"


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.values[service, name] = value

    def delete_password(self, service, name):
        del self.values[service, name]


@pytest.mark.parametrize(
    "value",
    [
        "http://gateway.example/mcp",
        "https://user:password@gateway.example/mcp",
        "https://gateway.example/mcp?token=secret",
        "https://gateway.example/mcp#secret",
        "https://gateway.example:bad/mcp",
        "https://gateway.example/\nother",
        "https://gateway.example\\evil/mcp",
        "file:///etc/passwd",
    ],
)
def test_server_rejects_unsafe_urls(value):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--server", value, "health"])


def test_server_normalizes_origin():
    assert cli.server_url("https://GATEWAY.example:443/mcp") == SERVER


@pytest.mark.parametrize(
    "arguments",
    [
        ["drawings", "scan", "--project", "C:/office"],
        ["drawings", "scan", "--project", f"prj_{UUID}", "--limit", "101"],
        ["drawings", "scan", "--project", f"prj_{UUID}", "--limit", "0"],
        ["plan", "../../drawing.dwg"],
        ["operations", "status", f"drw_{UUID}"],
        ["--timeout", "nan", "health"],
        ["--timeout", "inf", "health"],
        ["login", "--port", "0"],
        ["login", "--port", "65536"],
        ["publish", f"drw_{UUID}"],
    ],
)
def test_argument_validation(arguments):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(arguments)


def test_credential_expiry_endpoint_isolation_and_logout(monkeypatch):
    now = [1000]
    monkeypatch.setattr(cli_auth.time, "time", lambda: now[0])
    backend = MemoryKeyring()
    store = CredentialStore(SERVER, backend)

    async def exercise():
        await store.set_tokens(OAuthToken(access_token="dummy", expires_in=60))
        now[0] += 40
        assert (await store.get_tokens()).expires_in == 20
        now[0] += 20
        # Exactly exhausted must still read as past, never as one last usable moment.
        assert (await store.get_tokens()).expires_in < 0
        now[0] += 40
        assert (await store.get_tokens()).expires_in < 0
        other = CredentialStore("https://other.example/mcp", backend)
        assert await other.get_tokens() is None
        await other.set_tokens(OAuthToken(access_token="other", expires_in=60))
        await store.set_client_info(
            OAuthClientInformationFull(
                client_id="client",
                redirect_uris=["http://127.0.0.1:43817/callback"],
            )
        )
        store._write("discovery", {"example": True})
        store.clear()
        store.clear()  # idempotent
        assert await store.get_tokens() is None
        assert await store.get_client_info() is None
        assert store._read("discovery") is None
        assert await other.get_tokens() is not None

    asyncio.run(exercise())


def test_keyring_failures_do_not_leak_secrets():
    class BrokenKeyring(MemoryKeyring):
        def set_password(self, *_):
            raise RuntimeError("secret-token-value")

    store = CredentialStore(SERVER, BrokenKeyring())
    with pytest.raises(CLIError) as error:
        asyncio.run(store.set_tokens(OAuthToken(access_token="secret-token-value")))
    assert error.value.exit_code == 4
    assert "secret-token-value" not in str(error.value)


def test_loopback_rejects_wrong_state_host_duplicates_then_accepts():
    async def exercise():
        async with LoopbackLogin(0) as login:
            login.state = "expected"

            async def request(query, host=None, path="/callback"):
                reader, writer = await asyncio.open_connection("127.0.0.1", login.port)
                writer.write(
                    (
                        f"GET {path}?{query} HTTP/1.1\r\n"
                        f"Host: {host or f'127.0.0.1:{login.port}'}\r\n\r\n"
                    ).encode()
                )
                await writer.drain()
                data = await reader.read()
                writer.close()
                await writer.wait_closed()
                return data

            assert b"400" in await request("code=x&state=bad")
            assert b"400" in await request("code=x&state=expected", host="evil.example")
            assert b"400" in await request("code=x&state=expected&state=expected")
            assert b"400" in await request("code=x&state=expected", path="/other")
            assert not login.result.done()
            assert b"200" in await request("code=test-code&state=expected")
            assert await login.callback() == ("test-code", "expected")
            assert b"400" in await request("code=test-code&state=expected")
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", login.port)

    asyncio.run(exercise())


def test_loopback_denial_and_cleanup():
    async def exercise():
        async with LoopbackLogin(0) as login:
            login.state = "expected"
            async with httpx.AsyncClient() as client:
                await client.get(login.redirect_uri + "?error=access_denied&state=expected")
            with pytest.raises(CLIError, match="not authorized"):
                await login.callback()

    asyncio.run(exercise())


def test_main_json_errors_redact_nested_exceptions(monkeypatch, capsys):
    async def broken(_):
        raise ExceptionGroup("secret", [RuntimeError("secret-access-token")])

    monkeypatch.setattr(cli, "run", broken)
    assert cli.main(["--json", "health"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "secret" not in output.err
    assert json.loads(output.err)["error"]["code"] == "request_failed"


@pytest.mark.parametrize(
    "state,code",
    [
        ("CREATED", 0),
        ("SUCCEEDED", 0),
        ("FAILED", 1),
        ("EXPIRED", 1),
        ("ATTENTION_REQUIRED", 1),
    ],
)
def test_operation_exit_code(monkeypatch, capsys, state, code):
    async def result(_):
        return {"state": state}

    monkeypatch.setattr(cli, "run", result)
    assert cli.main(["--json", "operations", "status", f"op_{UUID}"]) == code
    assert json.loads(capsys.readouterr().out)["state"] == state


def test_no_tokens_requires_login_without_network(monkeypatch):
    monkeypatch.setattr(cli_auth, "native_keyring", MemoryKeyring)
    with pytest.raises(CLIError) as error:
        asyncio.run(cli.execute(cli.parser().parse_args(["workstations", "list"])))
    assert error.value.exit_code == 3


def test_https_hook_blocks_oauth_downgrade():
    with pytest.raises(CLIError, match="non-HTTPS"):
        asyncio.run(cli.require_https(httpx.Request("POST", "http://identity.example/token")))


def test_complete_oauth_mcp_flow_all_tools_and_cross_process_refresh(monkeypatch):
    """Real SDK/HTTP transport with mock HTTPS responses, not a live AutoCAD claim."""
    backend = MemoryKeyring()
    monkeypatch.setattr(cli_auth, "native_keyring", lambda: backend)
    calls, token_grants, registrations = [], [], []

    class BrowserLogin:
        def __init__(self, port):
            self.redirect_uri = f"http://127.0.0.1:{port}/callback"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def redirect(self, url):
            query = parse_qs(urlsplit(url).query)
            assert query["code_challenge_method"] == ["S256"]
            assert len(query["code_challenge"][0]) == 43
            assert query["scope"] == ["cadplot.read"]
            self.state = query["state"][0]

        async def callback(self):
            return "test-code", self.state

    monkeypatch.setattr(cli, "LoopbackLogin", BrowserLogin)

    def handler(request):
        path = request.url.path
        if path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if path.startswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(
                200,
                json={
                    "resource": SERVER,
                    "authorization_servers": [ISSUER],
                    "scopes_supported": ["cadplot.read"],
                },
            )
        if path.startswith("/.well-known/oauth-authorization-server"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": ISSUER + "/authorize",
                    "token_endpoint": ISSUER + "/oauth/token",
                    "registration_endpoint": ISSUER + "/register",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        if path == "/register":
            data = json.loads(request.content)
            assert data["token_endpoint_auth_method"] == "none"
            registrations.append(data)
            return httpx.Response(201, json={**data, "client_id": "test-client"})
        if path == "/oauth/token":
            assert request.url.host == "identity.example"
            data = parse_qs(request.content.decode())
            grant = data["grant_type"][0]
            token_grants.append(grant)
            if grant == "authorization_code":
                assert len(data["code_verifier"][0]) >= 43
            else:
                assert data["refresh_token"] == ["test-refresh"]
            return httpx.Response(
                200,
                json={
                    "access_token": "test-access",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": "cadplot.read",
                    **({"refresh_token": "test-refresh"} if grant == "authorization_code" else {}),
                },
            )
        assert path == "/mcp", f"Unexpected endpoint: {path}"
        if request.headers.get("authorization") != "Bearer test-access":
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": 'Bearer resource_metadata="https://gateway.example/.well-known/'
                    'oauth-protected-resource/mcp", scope="cadplot.read"'
                },
            )
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(405)
        message = json.loads(request.content)
        method = message["method"]
        if method.startswith("notifications/"):
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "list_workstations", "inputSchema": {"type": "object"}}]}
        else:
            assert method == "tools/call"
            calls.append(message["params"])
            result = {
                "content": [{"type": "text", "text": '{"state":"CREATED"}'}],
                "structuredContent": {"state": "CREATED"},
                "isError": False,
            }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        cli.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    commands = [
        (["workstations", "list"], "list_workstations", {}),
        (
            ["workstations", "validate", f"ws_{UUID}"],
            "validate_environment",
            {"workstation_id": f"ws_{UUID}"},
        ),
        (
            ["projects", "list", "--workstation", f"ws_{UUID}"],
            "list_projects",
            {"workstation_id": f"ws_{UUID}"},
        ),
        (
            ["drawings", "scan", "--project", f"prj_{UUID}", "--no-recursive", "--limit", "5"],
            "scan_drawings",
            {"project_id": f"prj_{UUID}", "recursive": False, "limit": 5},
        ),
        (["drawings", "inspect", f"drw_{UUID}"], "inspect_drawing", {"drawing_id": f"drw_{UUID}"}),
        (
            ["plan", f"drw_{UUID}", "--dry-run"],
            "create_publish_plan",
            {"drawing_id": f"drw_{UUID}"},
        ),
        (["operations", "status", f"op_{UUID}"], "get_operation", {"operation_id": f"op_{UUID}"}),
    ]

    async def run(arguments):
        return await cli.execute(cli.parser().parse_args(["--server", SERVER, *arguments]))

    assert asyncio.run(run(["health"]))["status"] == "ready"
    assert asyncio.run(run(["login"]))["status"] == "authenticated"
    for arguments, tool, fields in commands:
        assert asyncio.run(run(arguments)) == {"state": "CREATED"}
        assert calls[-1]["name"] == tool
        assert calls[-1]["arguments"] == fields
    assert len(calls) == 7  # each enqueue once; no hidden retries or polling
    assert len(registrations) == 1
    store = CredentialStore(SERVER, backend)
    saved = store._read("tokens")
    saved["expires_at"] = 0
    store._write("tokens", saved)
    asyncio.run(run(["tools"]))
    assert token_grants == ["authorization_code", "refresh_token"]
    assert asyncio.run(store.get_tokens()).refresh_token == "test-refresh"
    assert len(registrations) == 1
    asyncio.run(run(["logout"]))
    assert not backend.values
