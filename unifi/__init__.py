"""Selective policies around the UniFi implementation shipped with Core."""

from homeassistant.helpers import config_validation as cv, issue_registry as ir

from .adapter_bootstrap import async_native_module, async_prepare, native_attribute

CONFIG_SCHEMA = cv.config_entry_only_config_schema("unifi")


async def async_setup(hass, config):
    """Prepare the adapter before the native integration creates any entities."""
    native = await async_prepare(hass, config)
    ir.async_delete_issue(hass, "unifi", "local_override_outdated")
    setup = getattr(native["homeassistant.components.unifi"], "async_setup", None)
    if setup is None:
        return True
    return await setup(hass, config)


async def async_setup_entry(hass, config_entry):
    """Use the current Core setup with the validated selective hooks installed."""
    native = await async_native_module(hass)
    return await native.async_setup_entry(hass, config_entry)


async def async_unload_entry(hass, config_entry):
    """Delegate unload without maintaining a second connection lifecycle."""
    native = await async_native_module(hass)
    return await native.async_unload_entry(hass, config_entry)


async def async_migrate_entry(hass, config_entry):
    """Follow migrations supplied by the current Core version."""
    native = await async_native_module(hass)
    migrate = getattr(native, "async_migrate_entry", None)
    if migrate is not None:
        return await migrate(hass, config_entry)
    return None


async def async_remove_config_entry_device(hass, config_entry, device_entry):
    """Keep the native device removal rules."""
    native = await async_native_module(hass)
    return await native.async_remove_config_entry_device(
        hass, config_entry, device_entry
    )


def __getattr__(name):
    """Preserve access to native types/constants for dependent integrations."""
    return native_attribute("", name)
