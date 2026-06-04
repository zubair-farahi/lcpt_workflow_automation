"""CP Suite OAuth2 token provider.

Obtains a Bearer (JWT) access token from the IdentityServer using the
password grant, then caches it until shortly before expiry.

Token endpoint (staging):
    POST https://stg-id.itscomply.com/connect/token
    Content-Type: application/x-www-form-urlencoded
    grant_type=password & client_id & client_secret & username & password

TODO: A staging user (username/password) must be provisioned by the CP Suite
team before this works. See James Boullion's note in the AIP-388 thread.
"""

from __future__ import annotations

import threading
import time

import httpx
import structlog

from ...domain.errors import CpSuiteAuthError

log = structlog.get_logger()


class CpSuiteTokenProvider:
    """Thread-safe, cached OAuth2 password-grant token provider."""

    def __init__(
        self,
        identity_server: str,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        grant_type: str = "password",
        refresh_margin_seconds: int = 30,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._token_url = f"{identity_server.rstrip('/')}/connect/token"
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
        self._grant_type = grant_type
        self._refresh_margin = refresh_margin_seconds
        self._timeout = timeout_seconds

        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_at: float = 0.0  # epoch seconds

    def get_token(self) -> str:
        """Return a valid access token, fetching/refreshing if needed."""
        with self._lock:
            if self._access_token and time.time() < self._expires_at:
                return self._access_token
            return self._fetch_token()

    def _fetch_token(self) -> str:
        if not self._username or not self._password:
            raise CpSuiteAuthError(
                "CP Suite username/password not configured. "
                "A staging user must be provisioned (see AIP-388 thread). "
                "Set CP_SUITE_USERNAME and CP_SUITE_PASSWORD."
            )

        data = {
            "grant_type": self._grant_type,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "username": self._username,
            "password": self._password,
        }
        try:
            resp = httpx.post(
                self._token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise CpSuiteAuthError(f"CP Suite token request failed: {exc}") from exc

        if resp.status_code != 200:
            raise CpSuiteAuthError(
                f"CP Suite token endpoint returned {resp.status_code}: {resp.text[:300]}"
            )

        payload = resp.json()
        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 3600)
        if not token:
            raise CpSuiteAuthError(f"CP Suite token response missing access_token: {payload}")

        self._access_token = token
        self._expires_at = time.time() + max(0, int(expires_in) - self._refresh_margin)
        # Never log the token itself
        log.info("cp_suite_token_acquired", expires_in=expires_in)
        return token
