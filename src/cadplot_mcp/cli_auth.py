"""Native credential storage and loopback OAuth for the public CadPlot CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sys
import time
import webbrowser
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)


class CLIError(Exception):
    """An intentionally public, credential-free error."""

    def __init__(self, code: str, message: str, exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def native_keyring():
    """Do not load configurable/third-party backends that might store plaintext."""
    try:
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            backend = WinVaultKeyring()
            backend.persist = "local machine"
        elif sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            backend = Keyring()
        else:
            from keyring.backends.SecretService import Keyring

            backend = Keyring()
        if backend.priority <= 0:
            raise RuntimeError("unavailable")
        return backend
    except Exception:
        raise CLIError(
            "credential_store_unavailable",
            "Unlock your OS credential store (Windows Credential Manager, macOS Keychain, "
            "or Linux Secret Service). Plaintext token storage is not supported.",
            4,
        ) from None


class CredentialStore:
    """Separate endpoint-bound entries for tokens and dynamic client registration."""

    def __init__(self, server: str, backend=None):
        self.backend = backend if backend is not None else native_keyring()
        self.service = "cadplot-cli:" + hashlib.sha256(server.encode()).hexdigest()

    def _read(self, name: str) -> dict | None:
        try:
            value = self.backend.get_password(f"{self.service}:{name}", "cadplot")
            return json.loads(value) if value is not None else None
        except Exception:
            raise CLIError(
                "credential_store_error", "Cannot read saved CLI credentials.", 4
            ) from None

    def _write(self, name: str, value: dict) -> None:
        try:
            self.backend.set_password(
                f"{self.service}:{name}", "cadplot", json.dumps(value, separators=(",", ":"))
            )
        except Exception:
            raise CLIError(
                "credential_store_error", "Cannot securely save CLI credentials.", 4
            ) from None

    async def get_tokens(self) -> OAuthToken | None:
        data = self._read("tokens")
        if data is None:
            return None
        try:
            token = OAuthToken.model_validate(data["token"])
            # Never restart a persisted token's lifetime on every CLI invocation.
            expiry = data["expires_at"]
            remaining = int(expiry - time.time()) if expiry is not None else 0
            # An exhausted lifetime must land strictly in the past: the SDK counts an expiry
            # equal to now as still valid, and a coarse clock can hold time.time() steady long
            # enough to spend a dead access token instead of refreshing it.
            token.expires_in = remaining if remaining > 0 else -1
            return token
        except Exception:
            raise CLIError(
                "credential_store_error", "Saved tokens are invalid; run cadplot logout first.", 4
            ) from None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._write(
            "tokens",
            {
                "token": tokens.model_dump(mode="json", exclude_none=True),
                "expires_at": time.time() + tokens.expires_in
                if tokens.expires_in is not None
                else None,
            },
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._read("client")
        if data is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(data)
        except Exception:
            raise CLIError(
                "credential_store_error", "Saved client is invalid; run cadplot logout first.", 4
            ) from None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._write("client", client_info.model_dump(mode="json", exclude_none=True))

    def clear(self) -> None:
        for name in ("tokens", "client", "discovery"):
            try:
                if self.backend.get_password(f"{self.service}:{name}", "cadplot") is not None:
                    self.backend.delete_password(f"{self.service}:{name}", "cadplot")
            except Exception:
                raise CLIError(
                    "credential_store_error", "Cannot remove saved CLI credentials.", 4
                ) from None


class PersistentOAuthProvider(OAuthClientProvider):
    """Adapt MCP 1.29's in-memory OAuth context to independent CLI processes.

    Pin/test these SDK hooks: saved-token expiry and discovered refresh endpoints
    are not restored by the SDK's default _initialize implementation.
    """

    async def _initialize(self) -> None:
        await super()._initialize()
        store = self.context.storage
        assert isinstance(store, CredentialStore)
        data = store._read("discovery")
        if data:
            try:
                self.context.oauth_metadata = OAuthMetadata.model_validate(data["authorization"])
                resource = ProtectedResourceMetadata.model_validate(data["resource"])
                await self._validate_resource_match(resource)
                self.context.protected_resource_metadata = resource
                self.context.auth_server_url = str(resource.authorization_servers[0])
            except Exception:
                raise CLIError(
                    "credential_store_error",
                    "Saved OAuth metadata is invalid; logout and sign in.",
                    4,
                ) from None
        if self.context.current_tokens:
            self.context.update_token_expiry(self.context.current_tokens)
            if not data:
                # Never send refresh credentials to an invented default /token endpoint.
                self.context.clear_tokens()

    async def _handle_token_response(self, response: httpx.Response) -> None:
        store = self.context.storage
        assert isinstance(store, CredentialStore)
        if not self.context.oauth_metadata or not self.context.protected_resource_metadata:
            raise CLIError(
                "discovery_required", "The server must advertise OAuth resource metadata."
            )
        store._write(
            "discovery",
            {
                "authorization": self.context.oauth_metadata.model_dump(
                    mode="json",
                    exclude_none=True,
                    include={
                        "issuer",
                        "authorization_endpoint",
                        "token_endpoint",
                        "response_types_supported",
                        "token_endpoint_auth_methods_supported",
                    },
                ),
                "resource": self.context.protected_resource_metadata.model_dump(
                    mode="json",
                    exclude_none=True,
                    include={"resource", "authorization_servers"},
                ),
            },
        )
        await super()._handle_token_response(response)

    async def _handle_refresh_response(self, response: httpx.Response) -> bool:
        previous = self.context.current_tokens
        refreshed = await super()._handle_refresh_response(response)
        current = self.context.current_tokens
        if refreshed and current and previous and not current.refresh_token:
            current.refresh_token = previous.refresh_token
            await self.context.storage.set_tokens(current)
        return refreshed


class LoopbackLogin:
    """An ephemeral listener on a fixed registered loopback port, never 0.0.0.0."""

    def __init__(self, port: int = 43817):
        self.port = port
        self.state: str | None = None

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/callback"

    async def __aenter__(self):
        self.result = asyncio.get_running_loop().create_future()
        try:
            self.server = await asyncio.start_server(
                self._handle, "127.0.0.1", self.port, limit=8192
            )
        except OSError:
            raise CLIError(
                "callback_port_unavailable", "Login port is busy; retry with login --port PORT."
            ) from None
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_):
        self.server.close()
        await self.server.wait_closed()
        if not self.result.done():
            self.result.cancel()

    async def redirect(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or any(ord(c) < 32 for c in url):
            raise CLIError("unsafe_authorization_url", "The identity provider must use HTTPS.")
        states = parse_qs(parsed.query).get("state", [])
        if len(states) != 1 or not states[0]:
            raise CLIError("invalid_authorization_state", "OAuth state was not provided.")
        self.state = states[0]
        print("Complete sign-in in your browser. If it does not open, visit:", file=sys.stderr)
        print(url, file=sys.stderr)
        await asyncio.to_thread(webbrowser.open, url)

    async def callback(self) -> tuple[str, str | None]:
        try:
            result = await asyncio.wait_for(asyncio.shield(self.result), timeout=300)
        except TimeoutError:
            raise CLIError(
                "login_timeout", "Sign-in timed out; run cadplot login again.", 5
            ) from None
        if isinstance(result, CLIError):
            raise result
        return result

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, body = "400 Bad Request", "Invalid sign-in callback."
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            lines = request.decode("ascii").split("\r\n")
            method, target, _version = lines[0].split(" ")
            parsed = urlsplit(target)
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
            hosts = [line[5:].strip() for line in lines[1:] if line.lower().startswith("host:")]
            state = query.get("state", [])
            valid = (
                method == "GET"
                and not parsed.scheme
                and not parsed.netloc
                and parsed.path == "/callback"
                and hosts == [f"127.0.0.1:{self.port}"]
                and len(state) == 1
                and self.state is not None
                and secrets.compare_digest(state[0], self.state)
                and not self.result.done()
            )
            codes = query.get("code", [])
            if valid and "error" in query:
                self.result.set_result(CLIError("login_denied", "Sign-in was not authorized.", 3))
                body = "Sign-in was not authorized. Return to the terminal."
            elif valid and len(codes) == 1 and codes[0]:
                self.result.set_result((codes[0], state[0]))
                status, body = "200 OK", "Sign-in received. Return to the terminal for the result."
        except (ValueError, UnicodeError, asyncio.LimitOverrunError, EOFError, TimeoutError):
            pass
        finally:
            payload = body.encode("utf-8")
            try:
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(payload)}\r\nCache-Control: no-store\r\n"
                    "Connection: close\r\n\r\n".encode()
                    + payload
                )
                await writer.drain()
            except (OSError, ConnectionError):
                pass
            writer.close()
            await writer.wait_closed()
