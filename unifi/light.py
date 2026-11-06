"""Delegate the light platform to the installed Home Assistant UniFi."""

from .adapter_bootstrap import async_native_module, native_attribute


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the current built-in platform with selective policy installed."""
    native = await async_native_module(hass, "light")
    return await native.async_setup_entry(hass, config_entry, async_add_entities)


def __getattr__(name):
    """Retain native platform capabilities such as PARALLEL_UPDATES."""
    return native_attribute("light", name)
