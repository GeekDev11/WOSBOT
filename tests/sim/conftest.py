"""Fixtures for the SimCord UI suite. Run with: pytest tests/sim --sim"""
import logging
import socket
import traceback
from urllib.parse import urlsplit

import aiohttp
import pytest
import pytest_asyncio

import sim_harness

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Keep UI simulations offline, including background API synchronization."""
    import asyncio
    from cogs.gift_operationsapi import GiftCodeAPI
    from cogs.bot_health import BotHealth, STATUS_WARNING

    async def offline_health_probe(self):
        # Monitoring probes test real external services, not UI routing.
        # Keep Bot Health's presentation available without network traffic.
        return {"status": STATUS_WARNING, "message": "Offline simulation"}

    monkeypatch.setattr(BotHealth, "_probe_wos_api", offline_health_probe)
    monkeypatch.setattr(BotHealth, "_probe_gift_distribution_api", offline_health_probe)

    async def idle_external_gift_sync(self):
        # The real cog is loaded so its menus and handlers remain intact,
        # but UI tests must never contact the shared Gift Code distribution
        # service in the background. Production code is untouched.
        await asyncio.Event().wait()

    monkeypatch.setattr(GiftCodeAPI, "start_api_check", idle_external_gift_sync)
    attempts = []
    real_connect = socket.socket.connect
    real_http_request = aiohttp.ClientSession._request

    async def guarded_http_request(session, method, url, **kwargs):
        parsed = urlsplit(str(url))
        if parsed.hostname in LOOPBACK:
            return await real_http_request(session, method, url, **kwargs)
        trace = " -> ".join(
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_stack(limit=18)
            if "site-packages/aiohttp" not in frame.filename
            and "/tests/sim/conftest.py" not in frame.filename
        )
        attempts.append((f"HTTP {method} {url}", trace))
        raise OSError(f"offline SimCord blocked {method} {url}")

    monkeypatch.setattr(aiohttp.ClientSession, "_request", guarded_http_request)

    def guarded_connect(sock, address):
        if isinstance(address, tuple) and address[0] in LOOPBACK:
            return real_connect(sock, address)
        # Include the call site for diagnosis, without allowing outbound
        # network access or weakening the per-test no-network assertion.
        origin = " -> ".join(
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_stack(limit=9)[:-1]
            if "/tests/sim/conftest.py" not in frame.filename
        )
        attempts.append((address, origin))
        raise OSError(f"network blocked in the SimCord suite: {address}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield
    assert not attempts, f"code tried to reach the network: {attempts}"


class _ErrorLog(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages = []

    def emit(self, record):
        self.messages.append(f"{record.name}: {record.getMessage()}")


@pytest.fixture
def logged_errors():
    handler = _ErrorLog()
    root = logging.getLogger()
    root.addHandler(handler)
    yield handler.messages
    root.removeHandler(handler)


def _sim_fixture(scenario, **options):
    @pytest_asyncio.fixture
    async def fixture(tmp_path, monkeypatch, logged_errors):
        monkeypatch.chdir(tmp_path)
        async with sim_harness.running(scenario, **options) as sim:
            sim.logged_errors = logged_errors
            yield sim
    return fixture


sim = _sim_fixture("populated")
empty_sim = _sim_fixture("empty")
sim_without_admin = _sim_fixture("populated", bot_admin=False)
