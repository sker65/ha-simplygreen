"""Config flow for SimplyGreen."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import SimplyGreenAuthError, SimplyGreenClient, SimplyGreenError
from .const import (
    CONF_BACKFILL_DAYS,
    CONF_MLIID,
    CONF_SURCHARGE_CT,
    CONF_VAT_PERCENT,
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_SURCHARGE_CT,
    DEFAULT_VAT_PERCENT,
    DOMAIN,
)


# Config entry titles are plain strings and cannot be translated by HA, so pick one here.
_ENTRY_TITLES = {
    "de": "SimplyGreen Messlokation {mliid}",
    "en": "SimplyGreen metering location {mliid}",
}


def _price_schema(defaults: Mapping[str, Any]) -> dict:
    """Surcharge and VAT fields, shared by the setup and the options flow."""
    return {
        vol.Required(
            CONF_SURCHARGE_CT, default=defaults.get(CONF_SURCHARGE_CT, DEFAULT_SURCHARGE_CT)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=100, step=0.01, unit_of_measurement="ct/kWh",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_VAT_PERCENT, default=defaults.get(CONF_VAT_PERCENT, DEFAULT_VAT_PERCENT)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=100, step=0.1, unit_of_measurement="%",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }


class SimplyGreenConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SimplyGreenOptionsFlow:
        return SimplyGreenOptionsFlow()

    async def _try_login(self, username: str, password: str) -> SimplyGreenClient:
        session = async_create_clientsession(self.hass, cookie_jar=aiohttp.CookieJar())
        client = SimplyGreenClient(session, username, password)
        await client.login()
        return client

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                client = await self._try_login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except SimplyGreenAuthError:
                errors["base"] = "invalid_auth"
            except (SimplyGreenError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                mliid = (user_input.get(CONF_MLIID) or "").strip() or client.discovered_mliid
                if not mliid:
                    errors[CONF_MLIID] = "mliid_required"
                else:
                    await self.async_set_unique_id(mliid)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=_ENTRY_TITLES.get(
                            self.hass.config.language.split("-")[0], _ENTRY_TITLES["en"]
                        ).format(mliid=mliid),
                        data={**user_input, CONF_MLIID: mliid},
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_MLIID, default=""): str,
                vol.Optional(CONF_BACKFILL_DAYS, default=DEFAULT_BACKFILL_DAYS): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=730)
                ),
                **_price_schema({}),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await self._try_login(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            except SimplyGreenAuthError:
                errors["base"] = "invalid_auth"
            except (SimplyGreenError, aiohttp.ClientError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


class SimplyGreenOptionsFlow(OptionsFlowWithReload):
    """Change surcharge and VAT later; the entry reloads automatically."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=vol.Schema(_price_schema(current)))
