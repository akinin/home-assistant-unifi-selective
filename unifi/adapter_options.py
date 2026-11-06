"""Selective client controls layered onto the current built-in options flow."""

from copy import copy
import operator
from typing import TYPE_CHECKING, Any, override

import probatio

from homeassistant.components.unifi.config_flow import (
    UnifiOptionsFlowHandler as NativeOptionsFlowHandler,
)
from homeassistant.components.unifi.const import (
    CONF_BLOCK_CLIENT,
    CONF_CLIENT_SOURCE,
    CONF_MORE_OPTIONS,
    CONF_TRACK_CLIENTS,
    CONF_TRACK_DEVICES,
)
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv, selector

from .adapter_policy import get_policy

if TYPE_CHECKING:
    from homeassistant.components.unifi.hub import UnifiHub

CONF_CLIENT_FILTER_MODE = "client_filter_mode"
CONF_CREATE_NETWORK_ENTITIES = "create_network_entities"
CLIENT_FILTER_ALL = "all"
CLIENT_FILTER_SELECTED = "selected"
CLIENT_FILTER_NONE = "none"


def _field_name(key: Any) -> Any:
    """Return a schema marker's field name."""
    return key.schema if isinstance(key, probatio.Marker) else key


def _replace_schema(
    original: probatio.Schema, fields: dict[Any, Any]
) -> probatio.Schema:
    """Keep the native schema's validation settings while changing its fields."""
    return probatio.Schema(fields, extra=original.extra, required=original.required)


def _client_labels(hub: UnifiHub, selected: list[str]) -> dict[str, str]:
    """Describe current clients and retain unavailable selections in the form."""
    clients: dict[str, str] = {}
    for client in hub.api.clients.values():
        details = [client.mac]
        if client.ip:
            details.insert(0, client.ip)
        if client.essid:
            details.append(client.essid)
        clients[client.mac] = (
            f"{client.name or client.hostname or 'Unknown'} ({' · '.join(details)})"
        )
    clients.update({mac: f"Unknown ({mac})" for mac in selected if mac not in clients})
    return dict(sorted(clients.items(), key=operator.itemgetter(1)))


class UnifiOptionsFlowHandler(NativeOptionsFlowHandler):
    """Extend only selection controls; inherit all native advanced options."""

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep upstream options and saving behavior with selective controls."""
        hub = self.config_entry.runtime_data
        policy = get_policy(hub)
        if user_input is not None:
            user_input = dict(user_input)
            mode = user_input.get(CONF_CLIENT_FILTER_MODE, policy.mode)
            user_input[CONF_CLIENT_FILTER_MODE] = mode
            user_input[CONF_TRACK_CLIENTS] = mode == CLIENT_FILTER_ALL
            user_input[CONF_TRACK_DEVICES] = user_input.get(
                CONF_CREATE_NETWORK_ENTITIES, policy.network
            )
            if mode == CLIENT_FILTER_NONE:
                user_input[CONF_CLIENT_SOURCE] = []
            return await super().async_step_init(user_input)

        result = await super().async_step_init()
        if (original := result.get("data_schema")) is None:
            return result
        clients = _client_labels(hub, self.options.get(CONF_CLIENT_SOURCE, []))
        fields: dict[Any, Any] = {
            probatio.Required(
                CONF_CLIENT_FILTER_MODE, default=policy.mode
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        CLIENT_FILTER_ALL,
                        CLIENT_FILTER_SELECTED,
                        CLIENT_FILTER_NONE,
                    ],
                    translation_key=CONF_CLIENT_FILTER_MODE,
                )
            ),
            probatio.Optional(
                CONF_CLIENT_SOURCE,
                default=self.options.get(CONF_CLIENT_SOURCE, []),
            ): cv.multi_select(clients),
            probatio.Optional(
                CONF_CREATE_NETWORK_ENTITIES, default=policy.network
            ): bool,
        }
        for key, value in original.schema.items():
            name = _field_name(key)
            if name in {CONF_TRACK_CLIENTS, CONF_TRACK_DEVICES, CONF_CLIENT_SOURCE}:
                continue
            if name == CONF_MORE_OPTIONS and isinstance(value, section):
                value = copy(value)
                value.schema = _replace_schema(
                    value.schema,
                    {
                        inner_key: inner_value
                        for inner_key, inner_value in value.schema.schema.items()
                        if _field_name(inner_key) != CONF_CLIENT_SOURCE
                    },
                )
            if name == CONF_BLOCK_CLIENT:
                # Opening/saving must retain unavailable control selections.
                labels = {
                    client.mac: (
                        f"{client.name or client.hostname or 'Unknown'} ({client.mac})"
                    )
                    for client in hub.api.clients.values()
                }
                controls = list(hub.config.option_block_clients)
                labels.update(
                    {mac: f"Unknown ({mac})" for mac in controls if mac not in labels}
                )
                key = probatio.Optional(CONF_BLOCK_CLIENT, default=controls)
                value = cv.multi_select(labels)
            fields[key] = value
        result["data_schema"] = _replace_schema(original, fields)
        return result
