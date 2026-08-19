"""Config flow for Sanremo coffee machines."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
import voluptuous as vol

from .api import SanremoClient, SanremoConnectionError, SanremoError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .profiles import async_detect_profile

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class SanremoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup of a Sanremo machine."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a host and verify we can talk to it."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            # Tolerate a pasted URL, since that is what the vendor app shows.
            host = host.removeprefix("http://").removeprefix("https://").rstrip("/")

            info, error = await self._async_probe(host)
            if error:
                errors["base"] = error
            elif info is not None:
                name, mac, model = info
                if mac:
                    await self.async_set_unique_id(format_mac(mac))
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                else:
                    # No MAC means no stable identity, so fall back to the host.
                    self._async_abort_entries_match({CONF_HOST: host})

                return self.async_create_entry(
                    title=f"{name} ({model})" if model else name,
                    data={CONF_HOST: host},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user point an existing entry at a new address."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            host = host.removeprefix("http://").removeprefix("https://").rstrip("/")

            info, error = await self._async_probe(host)
            if error:
                errors["base"] = error
            elif info is not None:
                _, mac, _ = info
                if mac:
                    await self.async_set_unique_id(format_mac(mac))
                    self._abort_if_unique_id_mismatch(reason="wrong_machine")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or dict(entry.data)
            ),
            errors=errors,
        )

    async def _async_probe(
        self, host: str
    ) -> tuple[tuple[str, str, str | None] | None, str | None]:
        """Connect to ``host`` and identify the machine."""
        session = async_get_clientsession(self.hass)
        client = SanremoClient(host, session)

        try:
            profile = await async_detect_profile(client)
            device = await profile.async_setup()
        except SanremoConnectionError:
            return None, "cannot_connect"
        except SanremoError:
            return None, "invalid_response"
        except Exception:
            _LOGGER.exception("Unexpected error probing Sanremo machine at %s", host)
            return None, "unknown"

        return (device.name, device.mac, device.model), None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SanremoOptionsFlow:
        """Return the options flow."""
        return SanremoOptionsFlow()


class SanremoOptionsFlow(OptionsFlowWithReload):
    """Let the user tune the polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=5, max=300)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
