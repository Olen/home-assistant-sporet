"""API client for Sporet."""

import logging
from typing import Any

import aiohttp

from .auth import SporetAuth, SporetAuthError
from .const import API_BASE_URL, API_SEGMENT_URL

_LOGGER = logging.getLogger(__name__)


class SporetAPIError(Exception):
    """Exception raised for API errors."""


class SporetAPI:
    """API client for Sporet."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: SporetAuth,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._auth = auth

    async def async_get_route_details(self, slope_id: str) -> dict[str, Any]:
        """Get route details from the API."""
        return await self._async_get_details(f"{API_BASE_URL}/{slope_id}/details", slope_id)

    async def async_get_segment_details(self, segment_id: str) -> dict[str, Any]:
        """Get segment details from the API."""
        return await self._async_get_details(
            f"{API_SEGMENT_URL}/{segment_id}/details", segment_id
        )

    async def _async_get_details(self, url: str, item_id: str) -> dict[str, Any]:
        """Fetch details, refreshing the access token once on a 401.

        A 401 raises SporetAuthError rather than SporetAPIError, so the
        coordinator can tell "the user has to log in again" apart from "the
        network is having a moment" - only the first is worth interrupting
        the user for.
        """
        try:
            token = await self._auth.async_get_access_token()
            for attempt in (1, 2):
                try:
                    data = await self._async_get(url, token)
                    break
                except _Unauthorized as err:
                    if attempt == 2:
                        raise SporetAuthError(
                            "The Sporet credentials were rejected even after "
                            "refreshing the access token"
                        ) from err
                    _LOGGER.debug("Got 401 for %s, refreshing the access token", url)
                    token = await self._auth.async_refresh()
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching details: %s", err)
            raise SporetAPIError(f"Error fetching details: {err}") from err

        _LOGGER.debug("API response for %s: %s", item_id, data)
        return data

    async def _async_get(self, url: str, token: str) -> dict[str, Any]:
        """One GET, turning a 401 into something the caller can act on."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        async with self._session.get(url, headers=headers) as response:
            if response.status == 401:
                raise _Unauthorized
            response.raise_for_status()
            return await response.json()


class _Unauthorized(Exception):
    """Internal: the API answered 401."""
