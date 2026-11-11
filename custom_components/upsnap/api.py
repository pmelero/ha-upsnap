"""Small async client for the UpSnap REST API (PocketBase)."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import json
import time
from typing import Any

import aiohttp

from .const import ACTION_SLEEP, ACTIONS

# Only ask for the fields we use. UpSnap device records also contain the
# Wake-on-LAN and Sleep-on-LAN passwords and the shutdown command, which must
# never reach Home Assistant.
DEVICE_FIELDS = "id,name,ip,mac,status,sol_enabled"
PAGE_SIZE = 200

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
# Sleep-on-LAN is a synchronous call in UpSnap.
SLEEP_TIMEOUT = aiohttp.ClientTimeout(total=40)

# Normal UpSnap users first, then the admin account (PocketBase >= 0.23).
AUTH_COLLECTIONS = ("users", "_superusers")
# Log in again when the token expires in less than this many seconds.
TOKEN_REFRESH_MARGIN = 3600


class UpSnapError(Exception):
    """Base error for the UpSnap client."""


class UpSnapConnectionError(UpSnapError):
    """UpSnap could not be reached."""


class UpSnapAuthError(UpSnapError):
    """The credentials were rejected."""


class UpSnapPermissionError(UpSnapError):
    """The user lacks the UpSnap permission for this request."""


class UpSnapNotFoundError(UpSnapError):
    """The device does not exist in UpSnap."""


@dataclass(frozen=True, slots=True)
class UpSnapDevice:
    """A device as returned by UpSnap."""

    id: str
    name: str
    ip: str
    mac: str
    status: str
    sol_enabled: bool

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> UpSnapDevice:
        """Build a device from a PocketBase record."""
        device_id = str(record["id"])
        return cls(
            id=device_id,
            name=str(record.get("name") or device_id),
            ip=str(record.get("ip") or ""),
            mac=str(record.get("mac") or ""),
            status=str(record.get("status") or ""),
            sol_enabled=bool(record.get("sol_enabled")),
        )


def token_expiry(token: str) -> float | None:
    """Return the expiry (unix time) of a PocketBase JWT, if it can be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


class UpSnapClient:
    """Talk to an UpSnap server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._url = url.rstrip("/")
        self._username = username
        self._password = password
        self._token: str | None = None
        self._token_expiry: float | None = None
        self._auth_collection: str | None = None
        self._login_lock = asyncio.Lock()
        self._last_device_count = 0

    @property
    def url(self) -> str:
        """Base URL of the UpSnap server."""
        return self._url

    async def login(self) -> None:
        """Log in, trying a normal user first and then the admin account."""
        collections = list(AUTH_COLLECTIONS)
        if self._auth_collection in collections:
            collections.remove(self._auth_collection)
            collections.insert(0, self._auth_collection)

        for collection in collections:
            status, data = await self._raw_request(
                "POST",
                f"/api/collections/{collection}/auth-with-password",
                json_body={"identity": self._username, "password": self._password},
                auth=False,
            )
            if status == 200 and isinstance(data, dict) and data.get("token"):
                self._token = str(data["token"])
                self._token_expiry = token_expiry(self._token)
                self._auth_collection = collection
                return
            if status == 429:
                raise UpSnapConnectionError("UpSnap is rate limiting login attempts")
            if status not in (400, 401, 403, 404):
                raise UpSnapError(f"Unexpected HTTP {status} while logging in")

        self._token = None
        self._token_expiry = None
        raise UpSnapAuthError("UpSnap rejected the username or password")

    async def get_devices(self) -> dict[str, UpSnapDevice]:
        """Return the devices the user is allowed to read, by id."""
        devices = await self._get_devices()
        if not devices and self._last_device_count:
            # PocketBase answers an expired or invalid token with an empty
            # list instead of an error: log in again and retry once.
            await self._relogin()
            devices = await self._get_devices()
        self._last_device_count = len(devices)
        return devices

    async def power(self, device_id: str, action: str) -> None:
        """Ask UpSnap to wake, shut down, reboot or sleep a device."""
        if action not in ACTIONS:
            raise ValueError(f"Unknown UpSnap action: {action}")
        await self._request(
            "GET",
            f"/api/upsnap/{action}/{device_id}",
            # UpSnap 5 waits for the result unless async is set; 4.x ignores it.
            params={"async": "true"},
            timeout=SLEEP_TIMEOUT if action == ACTION_SLEEP else REQUEST_TIMEOUT,
        )

    async def _get_devices(self) -> dict[str, UpSnapDevice]:
        devices: dict[str, UpSnapDevice] = {}
        page = 1
        while True:
            data = await self._request(
                "GET",
                "/api/collections/devices/records",
                params={
                    "page": page,
                    "perPage": PAGE_SIZE,
                    "sort": "name",
                    "fields": DEVICE_FIELDS,
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise UpSnapError("Unexpected response while listing devices")
            items = data["items"]
            for record in items:
                if isinstance(record, dict) and record.get("id"):
                    device = UpSnapDevice.from_record(record)
                    devices[device.id] = device
            total_pages = data.get("totalPages") or 0
            if len(items) < PAGE_SIZE or page >= total_pages:
                return devices
            page += 1

    async def _ensure_token(self) -> None:
        if self._token is not None and (
            self._token_expiry is None
            or self._token_expiry - time.time() > TOKEN_REFRESH_MARGIN
        ):
            return
        async with self._login_lock:
            if self._token is None or (
                self._token_expiry is not None
                and self._token_expiry - time.time() <= TOKEN_REFRESH_MARGIN
            ):
                await self.login()

    async def _relogin(self) -> None:
        async with self._login_lock:
            await self.login()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,
    ) -> Any:
        await self._ensure_token()
        status, data = await self._raw_request(
            method, path, params=params, timeout=timeout
        )
        if status == 401:
            await self._relogin()
            status, data = await self._raw_request(
                method, path, params=params, timeout=timeout
            )
        if 200 <= status < 300:
            return data

        message = data.get("message") if isinstance(data, dict) else None
        detail = f"HTTP {status}" + (f": {message}" if message else "")
        if status == 401:
            raise UpSnapAuthError(detail)
        if status == 403:
            raise UpSnapPermissionError(detail)
        if status == 404:
            raise UpSnapNotFoundError(detail)
        if status == 429:
            raise UpSnapConnectionError(detail)
        raise UpSnapError(detail)

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: bool = True,
        timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        if auth and self._token:
            # PocketBase accepts the raw token (a "Bearer " prefix is optional).
            headers["Authorization"] = self._token
        try:
            async with self._session.request(
                method,
                f"{self._url}{path}",
                params=params,
                json=json_body,
                headers=headers,
                timeout=timeout,
            ) as resp:
                if resp.content_type == "application/json":
                    data: Any = await resp.json()
                else:
                    data = await resp.text()
                return resp.status, data
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpSnapConnectionError(
                f"Error talking to UpSnap at {self._url}: {err!r}"
            ) from err
