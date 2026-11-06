"""Delegate diagnostics to the currently installed native integration."""

from .adapter_bootstrap import async_native_module


async def async_get_config_entry_diagnostics(hass, config_entry):
    """Return native redacted diagnostics."""
    native = await async_native_module(hass, "diagnostics")
    return await native.async_get_config_entry_diagnostics(hass, config_entry)
