from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager, suppress

import httpx
import pytest
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool

from cadplot_gateway.health import add_health_routes


class Result:
    def __init__(self, row) -> None:
        self._row = row

    def fetchone(self):
        return self._row


class ReadyConnection:
    def __init__(self, *, safe_identity: bool = True, schema_current: bool = True) -> None:
        self.safe_identity = safe_identity
        self.schema_current = schema_current

    def execute(self, statement: str, params=None) -> Result:
        if "pg_catalog.pg_roles AS login" in statement:
            return Result(
                (
                    "cadplot_gateway_runtime",
                    "cadplot_gateway_login",
                    self.safe_identity is False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    True,
                    True,
                )
            )
        if "cadplot_gateway.schema_migrations" in statement:
            assert params is not None and len(params) == 3
            return Result((self.schema_current,))
        raise AssertionError("unexpected readiness query")


class ReadyPool:
    def __init__(self, connection: ReadyConnection, *, fails: bool = False) -> None:
        self._connection = connection
        self._fails = fails
        self.timeouts: list[float] = []

    @contextmanager
    def connection(self, *, timeout: float):
        self.timeouts.append(timeout)
        if self._fails:
            raise RuntimeError("sensitive database detail")
        yield self._connection


class BlockingReadyPool(ReadyPool):
    def __init__(self, connection: ReadyConnection) -> None:
        super().__init__(connection)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.calls = 0

    @contextmanager
    def connection(self, *, timeout: float):
        self.timeouts.append(timeout)
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=5.0):
            raise RuntimeError("readiness test release timed out")
        try:
            yield self._connection
        finally:
            self.finished.set()


@pytest.mark.asyncio
async def test_health_routes_are_public_bounded_and_secret_free() -> None:
    pool = ReadyPool(ReadyConnection())
    app = add_health_routes(Starlette(), pool, pool_timeout_seconds=2.5)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://mcp.cadplot.test",
    ) as client:
        live = await client.get("/healthz")
        ready = await client.get("/readyz")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert ready.headers["cache-control"] == "no-store"
    assert pool.timeouts == [2.5]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pool",
    [
        ReadyPool(ReadyConnection(safe_identity=False)),
        ReadyPool(ReadyConnection(schema_current=False)),
        ReadyPool(ReadyConnection(), fails=True),
    ],
)
async def test_readiness_fails_closed_without_exposing_database_errors(pool: ReadyPool) -> None:
    app = add_health_routes(Starlette(), pool, pool_timeout_seconds=1.0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://mcp.cadplot.test",
    ) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "sensitive" not in response.text


@pytest.mark.asyncio
async def test_readiness_is_single_flight_and_does_not_starve_protected_threads() -> None:
    pool = BlockingReadyPool(ReadyConnection())
    app = add_health_routes(Starlette(), pool, pool_timeout_seconds=1.0)
    protected_started = threading.Event()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://mcp.cadplot.test",
    ) as client:
        requests = [asyncio.create_task(client.get("/readyz")) for _ in range(48)]
        try:
            assert await asyncio.to_thread(pool.entered.wait, 1.0)
            protected = asyncio.create_task(run_in_threadpool(protected_started.set))
            assert await asyncio.to_thread(protected_started.wait, 1.0)
            await asyncio.sleep(0.15)
        finally:
            pool.release.set()

        responses = await asyncio.wait_for(asyncio.gather(*requests), timeout=2.0)
        await asyncio.wait_for(protected, timeout=1.0)

    assert pool.calls == 1
    assert any(response.status_code == 200 for response in responses)
    unavailable = [response for response in responses if response.status_code == 503]
    assert unavailable
    assert all(response.json() == {"status": "unavailable"} for response in unavailable)
    assert all(response.headers["cache-control"] == "no-store" for response in responses)


@pytest.mark.asyncio
async def test_cancelled_readiness_request_cannot_spawn_a_second_probe() -> None:
    pool = BlockingReadyPool(ReadyConnection())
    app = add_health_routes(Starlette(), pool, pool_timeout_seconds=1.0)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://mcp.cadplot.test",
    ) as client:
        creator = asyncio.create_task(client.get("/readyz"))
        assert await asyncio.to_thread(pool.entered.wait, 1.0)
        creator.cancel()
        with suppress(asyncio.CancelledError):
            await creator

        try:
            contender = await client.get("/readyz")
            assert contender.status_code == 503
            assert contender.json() == {"status": "unavailable"}
            assert pool.calls == 1
        finally:
            pool.release.set()
        assert await asyncio.to_thread(pool.finished.wait, 1.0)
        await asyncio.sleep(0)
        cached = await client.get("/readyz")

    assert cached.status_code == 200
    assert cached.json() == {"status": "ready"}
    assert pool.calls == 1
