"""Config flow for Sporet integration."""

import json
import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BASE_URL,
    API_SEGMENT_URL,
    CONF_BEARER_TOKEN,
    CONF_IS_SEGMENT,
    CONF_REFRESH_TOKEN,
    CONF_SLOPE_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Shown in the setup and reauth forms. Kept out of the translation strings
# because hassfest rejects URLs there.
COPY_COMMAND = "copy(localStorage['oidc.user:https://login.sporet.no:geodata-public'])"


def parse_credentials(pasted: str) -> dict[str, str | None]:
    """Work out the tokens from whatever the user pasted.

    The web app keeps its whole token set in a single localStorage key,
    `oidc.user:https://login.sporet.no:geodata-public`. Pasting that JSON is
    both easier than digging a header out of the network tab and the only way
    to get the refresh token, which is what stops the access token expiring
    after 30 days. A bare token still works, with or without a `Bearer`
    prefix or the whole `authorization:` header line.
    """
    pasted = pasted.strip()

    if pasted.startswith("{"):
        try:
            stored = json.loads(pasted)
        except ValueError:
            stored = {}
        if access_token := stored.get("access_token"):
            return {
                CONF_BEARER_TOKEN: access_token,
                CONF_REFRESH_TOKEN: stored.get("refresh_token"),
            }

    bearer_token = " ".join(pasted.split())
    if bearer_token.lower().startswith("authorization"):
        bearer_token = bearer_token.split(" ")[2].strip()
    if bearer_token.lower().startswith("bearer"):
        bearer_token = bearer_token.split(" ")[1].strip()
    return {CONF_BEARER_TOKEN: bearer_token, CONF_REFRESH_TOKEN: None}


def sanitize_slope_id(slope_id: str) -> str:
    """Allow user to paste the full URL to the slope."""
    # Slope  : https://sporet.no/share/Slope/10490?name=Bergsj%C3%B8-Randan-Nysetlia
    # Segment: https://sporet.no/share/SlopeSegment/131219?name=Segment
    if slope_id.lower().startswith("https:"):
        slope_id = slope_id.split("/")[5].split("?")[0].strip()
    return slope_id


async def validate_input(hass: HomeAssistant, bearer_token: str, slope_id: str) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    # bearer_token = data.get(CONF_BEARER_TOKEN, token)
    # slope_id = data[CONF_SLOPE_ID]

    session = async_get_clientsession(hass)
    slope_url = f"{API_BASE_URL}/{slope_id}/details"
    segment_url = f"{API_SEGMENT_URL}/{slope_id}/details"

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }

    is_segment = False
    try:
        async with session.get(slope_url, headers=headers) as response:
            _LOGGER.debug(f"Trying to get {slope_url}")
            if response.status == 401:
                _LOGGER.error(f"Error 401 for {slope_url}")
                raise InvalidAuth
            elif response.status == 404:
                _LOGGER.info(f"Error 404 for {slope_url} - trying again with {segment_url}")
                # Try again with segment URL
                async with session.get(segment_url, headers=headers) as response:
                    if response.status == 401:
                        _LOGGER.error(f"Error 401 for {segment_url}")
                        raise InvalidAuth
                    _LOGGER.debug(f"Response {response.status} from {segment_url}")
                    response.raise_for_status()
                    api_data = await response.json()
                    is_segment = True
            else:
                _LOGGER.debug(f"Response {response.status} from {slope_url}")
                response.raise_for_status()
                api_data = await response.json()

            _LOGGER.debug(api_data)
            # Extract data from the new API structure (top-level fields)
            slope_name = api_data.get("name", f"Segment {api_data.get('selectedSegment', {}).get('id')}")
            if is_segment:
                route_api_id = api_data.get("selectedSegment", {}).get("id")
            else:
                route_api_id = api_data.get("id")

            # Verify the route exists and ID matches
            if route_api_id != int(slope_id):
                raise CannotConnect

            # Return route name for display
            return {
                "title": slope_name,
                "slope_name": slope_name,
                CONF_IS_SEGMENT: is_segment,
            }

    except aiohttp.ClientResponseError as err:
        if err.status == 401:
            raise InvalidAuth
        raise CannotConnect
    except aiohttp.ClientError as err:
        _LOGGER.error("Error connecting to Sporet API: %s", err)
        raise CannotConnect


class SporetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sporet."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input.update(parse_credentials(user_input[CONF_BEARER_TOKEN]))

            try:
                # Test with a "dummy" slope ID
                info = await validate_input(self.hass, bearer_token=user_input[CONF_BEARER_TOKEN], slope_id=10000)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Set unique ID based on slope_id
                await self.async_set_unique_id(user_input[CONF_NAME])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=user_input,
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Sporet.no"): str,
                # vol.Required(CONF_SLOPE_ID): str,
                vol.Required(CONF_BEARER_TOKEN): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            description_placeholders={"copy_command": COPY_COMMAND},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """Handle credentials that no longer work."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for a new token and put the entry back to work."""
        errors: dict[str, str] = {}

        if user_input is not None:
            credentials = parse_credentials(user_input[CONF_BEARER_TOKEN])

            try:
                # Test with a "dummy" slope ID
                await validate_input(
                    self.hass,
                    bearer_token=credentials[CONF_BEARER_TOKEN],
                    slope_id=10000,
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Replace both tokens: a refresh token stored alongside a
                # rejected access token is spent too, and keeping it would
                # only fail again on the next refresh.
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates=credentials,
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_BEARER_TOKEN): str}),
            description_placeholders={"copy_command": COPY_COMMAND},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return SporetOptionsFlowHandler()


    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {"slope": SporetSubentryFlowHandler}


class SporetOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Sporet options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate the new bearer token
            user_input.update(parse_credentials(user_input[CONF_BEARER_TOKEN]))

            try:
                # Test with a "dummy" slope ID
                await validate_input(self.hass, bearer_token=user_input[CONF_BEARER_TOKEN], slope_id=10000)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Update the config entry with the new bearer token
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        CONF_BEARER_TOKEN: user_input[CONF_BEARER_TOKEN],
                        CONF_REFRESH_TOKEN: user_input[CONF_REFRESH_TOKEN],
                    },
                )
                return self.async_create_entry(title="", data={})

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BEARER_TOKEN,
                    default=self.config_entry.data.get(CONF_BEARER_TOKEN, ""),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
        )


class SporetSubentryFlowHandler(config_entries.ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying a slope."""
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.SubentryFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_SLOPE_ID] = sanitize_slope_id(user_input[CONF_SLOPE_ID])

            # Set unique ID based on slope_id
            unique_id = f"{self._get_entry().data[CONF_NAME]}_{user_input[CONF_SLOPE_ID]}"

            for existing_subentry in self._get_entry().subentries.values():
                if existing_subentry.unique_id == unique_id:
                    errors[CONF_SLOPE_ID] = "already_configured"

            if not errors:
                try:
                    info = await validate_input(self.hass, slope_id=user_input[CONF_SLOPE_ID], bearer_token=self._get_entry().data[CONF_BEARER_TOKEN])
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected exception")
                    errors["base"] = "unknown"
                else:
                    user_input[CONF_IS_SEGMENT] = info[CONF_IS_SEGMENT]
                    return self.async_create_entry(
                        title=info["title"],
                        data=user_input,
                        unique_id=unique_id,
                    )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SLOPE_ID): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
