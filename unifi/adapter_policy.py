"""Selective client policy, independent of the installed UniFi implementation."""

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

CLIENT_FILTER_ALL = "all"
CLIENT_FILTER_NONE = "none"
CLIENT_FILTER_SELECTED = "selected"
CONF_CLIENT_FILTER_MODE = "client_filter_mode"
CONF_CREATE_NETWORK_ENTITIES = "create_network_entities"
POLICY_ATTRIBUTE = "_unifi_selective_policy"

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClientPolicy:
    """The user-owned behavior layered over native UniFi options."""

    mode: str = CLIENT_FILTER_ALL
    selected: frozenset[str] = frozenset()
    control: frozenset[str] = frozenset()
    network: bool = True

    def allows_client(self, mac: str) -> bool:
        """Keep explicitly controlled clients, including in none mode."""
        return (
            self.mode == CLIENT_FILTER_ALL
            or mac in self.selected
            or mac in self.control
        )


def policy_from_options(options: Mapping[str, Any]) -> ClientPolicy:
    """Normalize current and legacy options without importing native internals.

    Unknown explicit modes are rejected instead of silently exposing clients.
    """
    selected = frozenset(options.get("client_source", []))
    mode = options.get(CONF_CLIENT_FILTER_MODE)
    if mode is None:
        if options.get("track_clients", True):
            mode = CLIENT_FILTER_ALL
        else:
            mode = CLIENT_FILTER_SELECTED if selected else CLIENT_FILTER_NONE
    if mode not in {CLIENT_FILTER_ALL, CLIENT_FILTER_SELECTED, CLIENT_FILTER_NONE}:
        raise ValueError(f"Unsupported UniFi client filter mode: {mode!r}")
    return ClientPolicy(
        mode=mode,
        selected=selected if mode != CLIENT_FILTER_NONE else frozenset(),
        control=frozenset(options.get("block_client", [])),
        network=options.get(
            CONF_CREATE_NETWORK_ENTITIES, options.get("track_devices", True)
        ),
    )


def get_policy(hub: Any) -> ClientPolicy:
    """Return the hub snapshot, retained across live config-entry changes."""
    if (policy := getattr(hub, POLICY_ATTRIBUTE, None)) is not None:
        return policy
    return policy_from_options(hub.config.entry.options)


@callback
def remove_owned_device(
    hub: Any,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    device: dr.DeviceEntry,
) -> None:
    """Remove this entry's physical record without touching another owner.

    Callers use config-entry-scoped registry lookups, which return the real
    owner-specific device record rather than a shared composite ID.
    """
    entry_id = hub.config.entry.entry_id
    if device.config_entry_id != entry_id:
        return
    entries = er.async_entries_for_device(
        entity_registry, device.id, include_disabled_entities=True
    )
    for entry in entries:
        if entry.config_entry_id == entry_id:
            entity_registry.async_remove(entry.entity_id)
    # A legacy foreign reference is more important than deleting an empty
    # record. Modern HA normally gives the other integration a separate record.
    if any(entry.config_entry_id != entry_id for entry in entries):
        return
    device_registry.async_remove_device(device.id)


@callback
def safe_prune(hub: Any) -> None:
    """Reconcile excluded records after native UniFi has loaded its inventory."""
    policy = get_policy(hub)
    if policy.mode == CLIENT_FILTER_ALL and policy.network:
        return
    api = hub.api
    coordinator = hub.entity_loader.get_data_update_coordinator(api.devices)
    if not coordinator.last_update_success:
        _LOGGER.debug("Skipping UniFi cleanup: network inventory is unavailable")
        return

    entry_id = hub.config.entry.entry_id
    entity_registry = er.async_get(hub.hass)
    device_registry = dr.async_get(hub.hass)
    network_macs = {device.mac for device in api.devices.values()}
    client_macs = set(api.clients) | set(api.clients_all)
    for device in dr.async_entries_for_config_entry(device_registry, entry_id):
        macs = {
            value
            for kind, value in device.connections
            if kind == dr.CONNECTION_NETWORK_MAC
        }
        if not macs:
            continue
        if macs & network_macs:
            if not policy.network:
                remove_owned_device(hub, entity_registry, device_registry, device)
            continue
        if policy.mode == CLIENT_FILTER_ALL or not macs & client_macs:
            continue
        if any(policy.allows_client(mac) for mac in macs):
            continue
        remove_owned_device(hub, entity_registry, device_registry, device)


@callback
def remove_stale_client(
    loader: Any,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    entity_id: str,
    mac: str,
) -> None:
    """Retain native retention logic while safely removing its stale record."""
    hub = loader.hub
    if device := device_registry.async_get_device_by_connection(
        (dr.CONNECTION_NETWORK_MAC, mac), hub.config.entry.entry_id
    ):
        remove_owned_device(hub, entity_registry, device_registry, device)
    # The tracker may predate device association, even if a MAC record exists.
    if (
        entry := entity_registry.async_get(entity_id)
    ) is not None and entry.config_entry_id == hub.config.entry.entry_id:
        entity_registry.async_remove(entity_id)
