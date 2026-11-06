"""Narrow compatibility hooks around the running Core's UniFi code.

All protocol, coordinator and entity implementations remain native. These hooks
only supply the selection policy and the user's access-block switch semantics.
"""

import asyncio
from dataclasses import replace
from functools import wraps
import inspect
from types import ModuleType
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.entity_platform import async_get_platforms

from .adapter_policy import (
    CLIENT_FILTER_ALL,
    POLICY_ATTRIBUTE,
    get_policy,
    policy_from_options,
    remove_stale_client,
    safe_prune,
)

_PATCHES: list[tuple[Any, str, Any]] = []
_NATIVE_MODULES: tuple[ModuleType, ...] | None = None


def _module(native: dict[str, ModuleType], relative: str) -> ModuleType:
    """Accept relative keys as well as fully qualified native module names."""
    if relative in native:
        return native[relative]
    full_name = f"homeassistant.components.unifi.{relative}"
    if full_name in native:
        return native[full_name]
    for module in native.values():
        if module.__name__ == full_name:
            return module
    raise RuntimeError(f"Required native UniFi module was not loaded: {relative}")


def _patch(target: Any, name: str, replacement: Any) -> None:
    """Remember descriptors, not bound methods, so rollback is exact."""
    original = inspect.getattr_static(target, name)
    _PATCHES.append((target, name, original))
    setattr(target, name, replacement)


def uninstall_patches() -> None:
    """Restore native definitions, primarily for isolated compatibility tests."""
    global _NATIVE_MODULES
    for target, name, original in reversed(_PATCHES):
        setattr(target, name, original)
    _PATCHES.clear()
    _NATIVE_MODULES = None


def install_patches(native: dict[str, ModuleType]) -> None:
    """Install a single, reversible set of hooks after contracts are verified."""
    global _NATIVE_MODULES
    config_module = _module(native, "hub.config")
    hub_module = _module(native, "hub.hub")
    loader_module = _module(native, "hub.entity_loader")
    entity_module = _module(native, "entity")
    tracker_module = _module(native, "device_tracker")
    sensor_module = _module(native, "sensor")
    switch_module = _module(native, "switch")
    modules = (
        config_module,
        hub_module,
        loader_module,
        entity_module,
        tracker_module,
        sensor_module,
        switch_module,
    )
    if _NATIVE_MODULES is not None:
        if modules != _NATIVE_MODULES:
            raise RuntimeError("UniFi adapter is already bound to different modules")
        return

    config_class = config_module.UnifiConfig
    hub_class = hub_module.UnifiHub
    loader_class = loader_module.UnifiEntityLoader
    tracker_class = tracker_module.UnifiScannerEntity
    entity_class = entity_module.UnifiEntity
    factory = inspect.getattr_static(config_class, "from_config_entry")
    if not isinstance(factory, classmethod):
        raise RuntimeError("Native UniFi config factory is no longer a classmethod")
    listener = inspect.getattr_static(hub_class, "async_config_entry_updated")
    if not isinstance(listener, staticmethod):
        raise RuntimeError("Native UniFi options listener is no longer static")
    original_factory = factory.__func__
    original_listener = listener.__func__
    original_initialize = hub_class.initialize
    original_should_add = loader_class._should_add_entity
    original_sensor_eligible = sensor_module._async_should_add_sensor
    original_tracker_state = tracker_class.async_initiate_state
    client_info = entity_module.async_client_device_info_fn
    device_info = entity_module.async_device_device_info_fn

    @wraps(original_factory)
    def config_from_entry(cls: type, entry: Any) -> Any:
        result = original_factory(cls, entry)
        policy = policy_from_options(entry.options)
        result.option_track_clients = policy.mode == CLIENT_FILTER_ALL
        result.option_track_devices = policy.network
        result.option_supported_clients = [
            mac
            for mac in entry.options.get("client_source", [])
            if mac in policy.selected
        ]
        return result

    def policy_allows(hub: Any, description: Any, obj_id: str) -> bool:
        policy = get_policy(hub)
        if description.device_info_fn is client_info:
            return policy.allows_client(obj_id)
        if description.device_info_fn is device_info:
            return policy.network
        return True

    @callback
    @wraps(original_should_add)
    def should_add(loader: Any, description: Any, obj_id: str) -> bool:
        return policy_allows(loader.hub, description, obj_id) and original_should_add(
            loader, description, obj_id
        )

    @callback
    @wraps(original_sensor_eligible)
    def sensor_eligible(hub: Any, description: Any, obj_id: str) -> bool:
        return policy_allows(hub, description, obj_id) and original_sensor_eligible(
            hub, description, obj_id
        )

    @wraps(original_initialize)
    async def initialize(hub: Any) -> None:
        setattr(hub, POLICY_ATTRIBUTE, policy_from_options(hub.config.entry.options))
        await original_initialize(hub)
        safe_prune(hub)

    @callback
    @wraps(original_tracker_state)
    def tracker_state(entity: Any) -> None:
        original_tracker_state(entity)
        if entity._obj_id in get_policy(entity.hub).selected:
            # Only supplies the default; HA retains an existing user's choice.
            entity._attr_entity_registry_enabled_default = True

    @wraps(original_listener)
    async def options_updated(hass: Any, config_entry: Any) -> None:
        hub = config_entry.runtime_data
        previous = get_policy(hub)
        updated = policy_from_options(config_entry.options)
        setattr(hub, POLICY_ATTRIBUTE, updated)
        if previous == updated:
            await original_listener(hass, config_entry)
            return
        hub.config = config_class.from_config_entry(config_entry)

        async def apply_options(entity: Any) -> None:
            if not policy_allows(hub, entity.entity_description, entity._obj_id):
                await entity.remove_item({entity._obj_id})
                return
            await entity.async_signal_options_updated()

        # Await removal before unloading; an async dispatcher races with reload
        # and lets HA retain disallowed entities as unavailable/restored.
        await asyncio.gather(
            *(
                apply_options(entity)
                for platform in async_get_platforms(hass, "unifi")
                for entity in tuple(platform.entities.values())
                if isinstance(entity, entity_class) and entity.hub is hub
            )
        )
        hass.config_entries.async_schedule_reload(config_entry.entry_id)

    block_descriptions = [
        item for item in switch_module.ENTITY_DESCRIPTIONS if item.key == "Block client"
    ]
    if len(block_descriptions) != 1:
        raise RuntimeError("Native UniFi block-client description changed")
    block = block_descriptions[0]
    original_control = block.control_fn
    original_state = block.is_on_fn

    @wraps(original_control)
    async def block_control(hub: Any, obj_id: str, target: bool) -> None:
        await original_control(hub, obj_id, not target)

    @wraps(original_state)
    def block_state(hub: Any, client: Any) -> bool:
        return not original_state(hub, client)

    inverted_block = replace(
        block,
        control_fn=block_control,
        is_on_fn=block_state,
        event_is_on=set(switch_module.CLIENT_BLOCKED),
    )
    descriptions = tuple(
        inverted_block if item is block else item
        for item in switch_module.ENTITY_DESCRIPTIONS
    )

    try:
        _patch(config_class, "from_config_entry", classmethod(config_from_entry))
        _patch(loader_class, "_should_add_entity", should_add)
        _patch(sensor_module, "_async_should_add_sensor", sensor_eligible)
        _patch(hub_class, "initialize", initialize)
        _patch(tracker_class, "async_initiate_state", tracker_state)
        _patch(hub_class, "async_config_entry_updated", staticmethod(options_updated))
        _patch(loader_class, "_remove_client", remove_stale_client)
        _patch(switch_module, "ENTITY_DESCRIPTIONS", descriptions)
        _patch(switch_module, "async_block_client_control_fn", block_control)
    except Exception:
        uninstall_patches()
        raise
    _NATIVE_MODULES = modules
