"""Config flow for the Sigenergy ESS mySigen cloud add-on."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from awesomeversion import AwesomeVersion
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.loader import IntegrationNotFound, async_get_integration

from .api import (
    REGIONS,
    MySigenAuthError,
    MySigenConnectionError,
    MySigenPermissionError,
    MySigenRateLimitError,
    MySigenSession,
    async_validate_login,
)
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_ALLOW_GRID_DISCONNECT,
    CONF_PLANT_ENTRY_ID,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_MODBUS_VERSION,
    MODBUS_CONF_DEVICE_TYPE,
    MODBUS_DEVICE_TYPE_PLANT,
    MODBUS_DOMAIN,
)

from .link import async_link_problem

_LOGGER = logging.getLogger(__name__)

_PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)

# Asked after sign-in and again under Reconfigure.
_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=MAX_SCAN_INTERVAL,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_ALLOW_CONTROL): BooleanSelector(),
        vol.Required(CONF_ALLOW_GRID_DISCONNECT): BooleanSelector(),
    }
)


def _options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stored options for the settings in a submitted form."""
    return {
        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
        CONF_ALLOW_CONTROL: bool(user_input.get(CONF_ALLOW_CONTROL)),
        CONF_ALLOW_GRID_DISCONNECT: bool(user_input.get(CONF_ALLOW_GRID_DISCONNECT)),
    }


class MySigenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Link a mySigen account to one existing Modbus plant."""

    VERSION = 1
    _data: dict[str, Any]
    _title: str

    async def _async_modbus_problem(self) -> str | None:
        """Return an abort reason when the Modbus integration is unusable."""
        try:
            integration = await async_get_integration(self.hass, MODBUS_DOMAIN)
        except IntegrationNotFound:
            return "modbus_missing"
        version = integration.version
        if version is None or version < AwesomeVersion(MIN_MODBUS_VERSION):
            return "modbus_too_old"
        return None

    def _plants(self) -> dict[str, str]:
        """Return {entry_id: name} for the configured Modbus plants."""
        return {
            entry.entry_id: entry.data.get(CONF_NAME) or entry.title
            for entry in self.hass.config_entries.async_entries(MODBUS_DOMAIN)
            if entry.data.get(MODBUS_CONF_DEVICE_TYPE) == MODBUS_DEVICE_TYPE_PLANT
        }

    async def _async_login_error(
        self,
        region: str,
        username: str,
        password: str,
        plant_entry_id: str | None = None,
    ) -> str | None:
        """Try one login and return a form error key, or None on success.

        With a plant, also require that the account's station contains one
        of that plant's inverters.
        """
        http = async_get_clientsession(self.hass)
        try:
            if plant_entry_id is None:
                await async_validate_login(http, region, username, password)
            else:
                session = MySigenSession(http, region, username, password)
                problem = await async_link_problem(self.hass, session, plant_entry_id)
                if problem is None:
                    # Make the read the add-on depends on, so an account
                    # without access is refused here and not after setup.
                    await session.async_override_status()
                return problem
        except MySigenAuthError as err:
            _LOGGER.warning("mySigen refused the account: %s", err)
            return "invalid_auth"
        except MySigenRateLimitError:
            return "rate_limited"
        except MySigenPermissionError as err:
            _LOGGER.warning("mySigen did not allow a read: %s", err)
            return "no_permission"
        except MySigenConnectionError as err:
            _LOGGER.warning("mySigen login check failed: %s", err)
            # A server error with a correct request is how the service
            # answers a login method it no longer accepts.
            if err.status is not None and err.status >= 500:
                return "login_rejected"
            return "cannot_connect"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the plant and enter the mySigen account."""
        if reason := await self._async_modbus_problem():
            return self.async_abort(reason=reason)
        plants = self._plants()
        if not plants:
            return self.async_abort(reason="no_plant")

        errors: dict[str, str] = {}
        if user_input is not None:
            plant_entry_id = user_input[CONF_PLANT_ENTRY_ID]
            if plant_entry_id not in plants:
                return self.async_abort(reason="no_plant")
            # One mySigen connection per Modbus plant.
            await self.async_set_unique_id(plant_entry_id)
            self._abort_if_unique_id_configured()
            error = await self._async_login_error(
                user_input[CONF_REGION],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                plant_entry_id,
            )
            if error is None:
                self._data = user_input
                self._title = f"{plants[plant_entry_id]} mySigen"
                return await self.async_step_settings()
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_PLANT_ENTRY_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=entry_id, label=name)
                            for entry_id, name in plants.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
                    SelectSelectorConfig(
                        options=list(REGIONS),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="region",
                    )
                ),
                vol.Required(CONF_USERNAME): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.EMAIL, autocomplete="username"
                    )
                ),
                vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
            }
        )
        suggested = {
            key: value
            for key, value in (user_input or {}).items()
            if key != CONF_PASSWORD
        }
        if not suggested and len(plants) == 1:
            suggested = {CONF_PLANT_ENTRY_ID: next(iter(plants))}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the polling interval and what Home Assistant may control.

        Shown once the account has been checked against the plant. The same
        choices stay under Reconfigure.
        """
        if user_input is not None:
            # Another flow may have linked this plant meanwhile.
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._title, data=self._data, options=_options(user_input)
            )
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(
                _SETTINGS_SCHEMA,
                {
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    CONF_ALLOW_CONTROL: False,
                    CONF_ALLOW_GRID_DISCONNECT: False,
                },
            ),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start again when the stored credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the current password of the stored account."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_login_error(
                entry.data[CONF_REGION],
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the account or the polling interval of an existing link."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME]
            # A blank password keeps the stored one.
            password = user_input.get(CONF_PASSWORD) or entry.data[CONF_PASSWORD]
            error = None
            if (username, password) != (
                entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
            ):
                error = await self._async_login_error(
                    entry.data[CONF_REGION],
                    username,
                    password,
                    entry.data[CONF_PLANT_ENTRY_ID],
                )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_USERNAME: username, CONF_PASSWORD: password},
                    options={**entry.options, **_options(user_input)},
                )
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.EMAIL, autocomplete="username"
                    )
                ),
                vol.Optional(CONF_PASSWORD): _PASSWORD_SELECTOR,
            }
        ).extend(_SETTINGS_SCHEMA.schema)
        suggested = {
            CONF_USERNAME: (user_input or entry.data)[CONF_USERNAME],
            CONF_SCAN_INTERVAL: (user_input or {}).get(
                CONF_SCAN_INTERVAL,
                entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ),
            CONF_ALLOW_CONTROL: (user_input or {}).get(
                CONF_ALLOW_CONTROL, entry.options.get(CONF_ALLOW_CONTROL) is True
            ),
            CONF_ALLOW_GRID_DISCONNECT: (user_input or {}).get(
                CONF_ALLOW_GRID_DISCONNECT,
                entry.options.get(CONF_ALLOW_GRID_DISCONNECT) is True,
            ),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )
