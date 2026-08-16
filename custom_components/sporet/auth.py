"""Access token handling for Sporet.

The access token issued by login.sporet.no lasts 30 days, after which the
integration used to simply stop working until a new one was pasted in by hand.
The web app asks for the `offline_access` scope, so a refresh token is issued
alongside it - and login.sporet.no accepts the refresh_token grant for its
public client without a secret. Given that refresh token, this keeps the access
token fresh on its own and the manual step never has to be repeated.
"""

import base64
import binascii
import json
import logging
from datetime import datetime, timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BEARER_TOKEN,
    CONF_REFRESH_TOKEN,
    OIDC_CLIENT_ID,
    OIDC_TOKEN_URL,
    TOKEN_MAX_AGE_SECONDS,
    TOKEN_REFRESH_MARGIN_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class SporetAuthError(Exception):
    """Raised when the stored credentials can no longer be used.

    Distinct from a transport problem: this one needs the user, not a retry.
    """


def _claims(token: str) -> dict:
    """Return a JWT's payload, or an empty dict if it cannot be read.

    Only the payload is decoded - the signature is the API's business, not
    ours, and all we want to know is when to refresh.
    """
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        return json.loads(decoded)
    except (IndexError, ValueError, binascii.Error):
        return {}


def _claim_time(token: str, claim: str) -> datetime | None:
    """Return a timestamp claim from an access token, if it has one."""
    value = _claims(token).get(claim)
    return dt_util.utc_from_timestamp(value) if value else None


def token_expiry(token: str) -> datetime | None:
    """Return when an access token expires, or None if it cannot be read."""
    return _claim_time(token, "exp")


class SporetAuth:
    """Keeps a usable access token for a config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        self._hass = hass
        self._session = session
        self._entry = entry

    @property
    def access_token(self) -> str:
        """The access token as currently stored."""
        return self._entry.data.get(CONF_BEARER_TOKEN, "")

    @property
    def refresh_token(self) -> str | None:
        """The refresh token, if the entry was set up with one."""
        return self._entry.data.get(CONF_REFRESH_TOKEN)

    async def async_get_access_token(self) -> str:
        """Return a usable token, refreshing first if it is time to.

        Two reasons to refresh, and the second is the one that matters: the
        access token lasts 30 days, but the refresh token expires on its own
        schedule and only using it resets that. So refresh long before the
        access token would run out, or the refresh token dies of old age first
        and the user is back to logging in by hand.
        """
        if not self.refresh_token:
            return self.access_token

        now = dt_util.utcnow()
        expires_at = token_expiry(self.access_token)
        issued_at = _claim_time(self.access_token, "iat")

        due = False
        if expires_at and now + timedelta(seconds=TOKEN_REFRESH_MARGIN_SECONDS) >= expires_at:
            _LOGGER.debug("Access token expires at %s, refreshing", expires_at)
            due = True
        elif issued_at and now - issued_at >= timedelta(seconds=TOKEN_MAX_AGE_SECONDS):
            _LOGGER.debug("Access token issued at %s, rotating the refresh token", issued_at)
            due = True

        if due:
            await self.async_refresh()
        return self.access_token

    async def async_refresh(self) -> str:
        """Exchange the refresh token for a new access token.

        Raises SporetAuthError if there is nothing to refresh with or the
        provider rejects it - in both cases only the user can fix it.
        """
        if not self.refresh_token:
            raise SporetAuthError(
                "The access token has expired and no refresh token is stored"
            )

        payload = {
            "grant_type": "refresh_token",
            "client_id": OIDC_CLIENT_ID,
            "refresh_token": self.refresh_token,
        }

        try:
            async with self._session.post(OIDC_TOKEN_URL, data=payload) as response:
                body = await response.json()
                if response.status != 200:
                    raise SporetAuthError(
                        f"Could not refresh the access token: "
                        f"{body.get('error_description', body.get('error', response.status))}"
                    )
        except aiohttp.ClientError as err:
            raise SporetAuthError(f"Could not reach {OIDC_TOKEN_URL}: {err}") from err

        access_token = body.get("access_token")
        if not access_token:
            raise SporetAuthError("The token endpoint returned no access token")

        # The provider rotates refresh tokens: the one just used is now spent,
        # so whatever came back has to be stored or the next refresh fails.
        self._hass.config_entries.async_update_entry(
            self._entry,
            data={
                **self._entry.data,
                CONF_BEARER_TOKEN: access_token,
                CONF_REFRESH_TOKEN: body.get("refresh_token", self.refresh_token),
            },
        )
        _LOGGER.debug("Refreshed the Sporet access token, now valid until %s",
                      token_expiry(access_token))
        return access_token
