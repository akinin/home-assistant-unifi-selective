"""Reuse current built-in connection flows with selective client options."""

from typing import override

from homeassistant.components.unifi import UnifiConfigEntry
from homeassistant.components.unifi.config_flow import (
    UnifiFlowHandler as NativeUnifiFlowHandler,
)
from homeassistant.core import callback

from .adapter_options import UnifiOptionsFlowHandler


class UnifiFlowHandler(NativeUnifiFlowHandler, domain="unifi"):
    """Keep discovery, site selection, reauth and reconfigure in Home Assistant."""

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: UnifiConfigEntry,
    ) -> UnifiOptionsFlowHandler:
        """Use native options augmented only with the selective controls."""
        return UnifiOptionsFlowHandler(config_entry)
