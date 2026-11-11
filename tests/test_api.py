"""Tests for the UpSnap API client against a fake UpSnap (PocketBase) server."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field, replace
import json
import time

from aiohttp import ClientSession, TCPConnector, ThreadedResolver, web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.upsnap.api import (
    DEVICE_FIELDS,
    UpSnapAuthError,
    UpSnapClient,
    UpSnapConnectionError,
    UpSnapError,
    UpSnapNotFoundError,
    UpSnapPermissionError,
    token_expiry,
)

# These tests talk to a fake UpSnap server on 127.0.0.1.
pytestmark = pytest.mark.usefixtures("socket_enabled")


def make_token(exp: float, serial: int = 0) -> str:
    """Return a JWT-shaped token with the given expiry."""

    def encode(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{encode({'alg': 'HS256'})}.{encode({'exp': exp, 'n': serial})}.signature"


@dataclass
class FakeUpSnap:
    """In-memory UpSnap server."""

    users: dict[str, str] = field(default_factory=lambda: {"homeassistant": "secret"})
    superusers: dict[str, str] = field(default_factory=lambda: {"admin@x.io": "admin"})
    device_count: int = 3
    token_lifetime: float = 7 * 86400
    power_status: int = 200
    login_status: int | None = None
    devices_html: bool = False
    reject_power_tokens: bool = False
    # Devices (by index) without a shutdown command, and the user's Power ids.
    no_shutdown_cmd: set[int] = field(default_factory=lambda: {2})
    power_ids: list[str] = field(
        default_factory=lambda: ["device000000000", "device000000001"]
    )
    permissions_status: int = 200
    filter_status: int = 200
    valid_tokens: set[str] = field(default_factory=set)
    logins: list[str] = field(default_factory=list)
    list_queries: list[dict[str, str]] = field(default_factory=list)
    permission_queries: list[dict[str, str]] = field(default_factory=list)
    power_calls: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    async def auth(self, request: web.Request) -> web.Response:
        """Log in to a users or _superusers collection."""
        collection = request.match_info["collection"]
        body = await request.json()
        self.logins.append(collection)
        if self.login_status is not None:
            return web.json_response({"message": "nope"}, status=self.login_status)
        accounts = self.users if collection == "users" else self.superusers
        if accounts.get(body["identity"]) == body["password"]:
            token = make_token(time.time() + self.token_lifetime, len(self.logins))
            self.valid_tokens.add(token)
            return web.json_response({"token": token, "record": {"id": "user1"}})
        return web.json_response(
            {"code": 400, "message": "Failed to authenticate.", "data": {}}, status=400
        )

    async def devices(self, request: web.Request) -> web.Response:
        """List devices, honouring page, perPage and fields."""
        self.list_queries.append(dict(request.query))
        if self.devices_html:
            # e.g. a reverse proxy answering with its own page
            return web.Response(
                text="<html>Bad gateway</html>", content_type="text/html"
            )
        if request.headers.get("Authorization") not in self.valid_tokens:
            # PocketBase hides records instead of failing for unauthorized lists.
            return web.json_response(
                {
                    "items": [],
                    "page": 1,
                    "perPage": 200,
                    "totalItems": 0,
                    "totalPages": 0,
                }
            )
        page, per_page = int(request.query["page"]), int(request.query["perPage"])
        records = [
            {
                "id": f"device{i:09d}",
                "name": f"PC {i}",
                "ip": f"192.168.1.{i}",
                "mac": f"aa:bb:cc:dd:ee:{i:02x}",
                "status": "online" if i % 2 else "offline",
                "sol_enabled": i == 1,
                "password": "wol-password",
                "shutdown_cmd": ""
                if i in self.no_shutdown_cmd
                else "net rpc shutdown -U user%password",
            }
            for i in range(self.device_count)
        ]
        if "filter" in request.query:
            if self.filter_status != 200:
                return web.json_response(
                    {"message": "Invalid filter."}, status=self.filter_status
                )
            assert request.query["filter"] == 'shutdown_cmd != ""'
            records = [record for record in records if record["shutdown_cmd"]]
        fields = request.query["fields"].split(",")
        items = [
            {key: value for key, value in record.items() if key in fields}
            for record in records[(page - 1) * per_page : page * per_page]
        ]
        return web.json_response(
            {
                "items": items,
                "page": page,
                "perPage": per_page,
                "totalItems": len(records),
                "totalPages": -(-len(records) // per_page),
            }
        )

    async def permissions(self, request: web.Request) -> web.Response:
        """Return the permissions record of the logged-in user."""
        self.permission_queries.append(dict(request.query))
        if self.permissions_status != 200:
            return web.json_response(
                {"message": "Not available."}, status=self.permissions_status
            )
        items = []
        if request.headers.get("Authorization") in self.valid_tokens:
            assert request.query["filter"] == "user.id = 'user1'"
            assert request.query["fields"] == "power"
            items = [{"power": self.power_ids}]
        return web.json_response(
            {"items": items, "page": 1, "perPage": 200, "totalPages": 1}
        )

    async def power(self, request: web.Request) -> web.Response:
        """Accept a power action."""
        if (
            self.reject_power_tokens
            or request.headers.get("Authorization") not in self.valid_tokens
        ):
            return web.json_response(
                {"message": "The request requires an authorization token."},
                status=401,
            )
        if self.power_status != 200:
            return web.json_response(
                {"message": "You are not allowed to perform this request."},
                status=self.power_status,
            )
        self.power_calls.append(
            (
                request.match_info["action"],
                request.match_info["id"],
                dict(request.query),
            )
        )
        return web.json_response({"id": request.match_info["id"], "status": "pending"})


@pytest.fixture
async def fake_upsnap() -> AsyncGenerator[tuple[FakeUpSnap, str]]:
    """Run the fake UpSnap server and return it with its URL."""
    fake = FakeUpSnap()
    app = web.Application()
    app.router.add_post("/api/collections/{collection}/auth-with-password", fake.auth)
    app.router.add_get("/api/collections/devices/records", fake.devices)
    app.router.add_get("/api/collections/permissions/records", fake.permissions)
    app.router.add_get("/api/upsnap/{action}/{id}", fake.power)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("/"))
    await server.close()


@pytest.fixture
async def http_session() -> AsyncGenerator[ClientSession]:
    """Return an aiohttp session for the fake server.

    The threaded resolver avoids aiodns, whose shutdown thread outlives the test on
    older Home Assistant versions; the fake server is reached by IP anyway.
    """
    connector = TCPConnector(resolver=ThreadedResolver())
    async with ClientSession(connector=connector) as session:
        yield session


async def test_login_and_list_devices(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """A normal user logs in and only the needed fields are requested."""
    fake, url = fake_upsnap
    client = UpSnapClient(http_session, url, "homeassistant", "secret")

    found = await client.get_devices()

    assert fake.logins == ["users"]
    assert fake.list_queries[0]["fields"] == DEVICE_FIELDS
    # Secrets are never requested: the shutdown command is only used in a filter.
    for query in fake.list_queries:
        assert "shutdown_cmd" not in query["fields"]
        assert "password" not in query["fields"]
    assert len(found) == 3
    device = found["device000000001"]
    assert device.name == "PC 1"
    assert device.status == "online"
    assert device.sol_enabled is True
    assert device.has_shutdown_cmd is True
    assert device.can_power is True
    assert not hasattr(device, "password")
    # PC 2 has no shutdown command and the user lacks the Power permission on it.
    assert found["device000000002"].has_shutdown_cmd is False
    assert found["device000000002"].can_power is False
    assert client.url == url.rstrip("/")


async def test_superuser_can_power_everything(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """The admin account has no permissions record and may power every device."""
    fake, url = fake_upsnap
    client = UpSnapClient(http_session, url, "admin@x.io", "admin")
    found = await client.get_devices()
    assert all(device.can_power for device in found.values())
    assert fake.permission_queries == []


async def test_unknown_capabilities(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """If UpSnap cannot answer the lookups, every device keeps every action."""
    fake, url = fake_upsnap
    fake.permissions_status = 404
    fake.filter_status = 400
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    found = await client.get_devices()
    assert all(device.can_power for device in found.values())
    assert all(device.has_shutdown_cmd for device in found.values())


async def test_no_readable_devices(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """Without readable devices nothing else is looked up."""
    fake, url = fake_upsnap
    fake.device_count = 0
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    assert await client.get_devices() == {}
    assert len(fake.list_queries) == 1
    assert fake.permission_queries == []


@pytest.mark.parametrize("lookup", ["filter_status", "permissions_status"])
async def test_capability_lookup_rate_limited(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession, lookup: str
) -> None:
    """Connection problems during the lookups fail the update instead of hiding."""
    fake, url = fake_upsnap
    setattr(fake, lookup, 429)
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    with pytest.raises(UpSnapConnectionError):
        await client.get_devices()


async def test_capabilities_cached(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """Lookups run once a minute, or as soon as a new device appears."""
    fake, url = fake_upsnap
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    await client.get_devices()
    await client.get_devices()
    assert len(fake.permission_queries) == 1

    fake.device_count = 4
    await client.get_devices()
    assert len(fake.permission_queries) == 2

    assert client._capabilities is not None
    client._capabilities = replace(client._capabilities, fetched_at=-1e9)
    await client.get_devices()
    assert len(fake.permission_queries) == 3


async def test_superuser_fallback(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """The admin account is tried after a normal user, then remembered."""
    fake, url = fake_upsnap
    client = UpSnapClient(http_session, url, "admin@x.io", "admin")
    await client.login()
    assert fake.logins == ["users", "_superusers"]
    fake.logins.clear()
    await client.login()
    assert fake.logins == ["_superusers"]


async def test_invalid_credentials(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """Wrong credentials raise UpSnapAuthError."""
    _, url = fake_upsnap
    with pytest.raises(UpSnapAuthError):
        await UpSnapClient(http_session, url, "homeassistant", "wrong").login()


async def test_invalid_token_gives_empty_list(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """An empty list after a non-empty one triggers one new login."""
    fake, url = fake_upsnap
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    await client.get_devices()
    fake.logins.clear()
    fake.valid_tokens.clear()  # e.g. UpSnap invalidated the token

    assert len(await client.get_devices()) == 3
    assert fake.logins == ["users"]


async def test_relogin_before_expiry(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """A token about to expire is renewed before the request."""
    fake, url = fake_upsnap
    fake.token_lifetime = 60
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    await client.login()
    await client.power("device000000001", "wake")
    assert fake.logins == ["users", "users"]


async def test_pagination(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """All pages are read."""
    fake, url = fake_upsnap
    fake.device_count = 450
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    assert len(await client.get_devices()) == 450
    pages = [q["page"] for q in fake.list_queries if q["fields"] == DEVICE_FIELDS]
    assert pages == ["1", "2", "3"]


async def test_power(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """Power actions use GET /api/upsnap/<action>/<id>?async=true."""
    fake, url = fake_upsnap
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    await client.power("device000000001", "wake")
    await client.power("device000000001", "sleep")
    assert fake.power_calls == [
        ("wake", "device000000001", {"async": "true"}),
        ("sleep", "device000000001", {"async": "true"}),
    ]
    with pytest.raises(ValueError, match="Unknown UpSnap action"):
        await client.power("device000000001", "explode")


async def test_power_relogin_on_401(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """A rejected token is renewed once and the action retried."""
    fake, url = fake_upsnap
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    await client.login()
    fake.valid_tokens.clear()
    fake.logins.clear()
    await client.power("device000000001", "shutdown")
    assert fake.logins == ["users"]
    assert fake.power_calls[0][0] == "shutdown"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, UpSnapPermissionError),
        (404, UpSnapNotFoundError),
        (429, UpSnapConnectionError),
        (500, UpSnapError),
    ],
)
async def test_power_errors(
    fake_upsnap: tuple[FakeUpSnap, str],
    http_session: ClientSession,
    status: int,
    error: type[Exception],
) -> None:
    """HTTP errors map to the client exceptions."""
    fake, url = fake_upsnap
    fake.power_status = status
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    with pytest.raises(error):
        await client.power("device000000001", "wake")


async def test_unreachable(http_session: ClientSession) -> None:
    """A closed port raises UpSnapConnectionError."""
    with pytest.raises(UpSnapConnectionError):
        await UpSnapClient(http_session, "http://127.0.0.1:9", "a", "b").login()


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, UpSnapConnectionError), (500, UpSnapError)],
)
async def test_login_unexpected_status(
    fake_upsnap: tuple[FakeUpSnap, str],
    http_session: ClientSession,
    status: int,
    error: type[Exception],
) -> None:
    """Rate limiting and server errors are not reported as wrong credentials."""
    fake, url = fake_upsnap
    fake.login_status = status
    with pytest.raises(error) as err:
        await UpSnapClient(http_session, url, "homeassistant", "secret").login()
    assert not isinstance(err.value, UpSnapAuthError)


async def test_unexpected_device_list(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """A page that is not an UpSnap answer raises UpSnapError."""
    fake, url = fake_upsnap
    fake.devices_html = True
    with pytest.raises(UpSnapError, match="Unexpected response"):
        await UpSnapClient(http_session, url, "homeassistant", "secret").get_devices()


async def test_token_rejected_after_relogin(
    fake_upsnap: tuple[FakeUpSnap, str], http_session: ClientSession
) -> None:
    """If UpSnap keeps rejecting fresh tokens the error is an auth error."""
    fake, url = fake_upsnap
    fake.reject_power_tokens = True
    client = UpSnapClient(http_session, url, "homeassistant", "secret")
    with pytest.raises(UpSnapAuthError):
        await client.power("device000000001", "wake")
    assert fake.logins == ["users", "users"]


def test_token_expiry() -> None:
    """The expiry is read from the token payload."""
    assert token_expiry(make_token(123.0)) == 123.0
    assert token_expiry("not-a-token") is None
