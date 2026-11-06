"""Compatibility checks must reject changed Core contracts before discovery."""

import importlib
from pathlib import Path

from custom_components.unifi.adapter_bootstrap import async_prepare
from custom_components.unifi.adapter_contracts import AdapterCompatibilityError
from custom_components.unifi.adapter_patches import install_patches, uninstall_patches
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.unifi.hub.hub import UnifiHub
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .conftest import ConfigEntryFactoryType


async def test_runtime_uses_installed_core_backend(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """A configured instance is the real Core hub, not a copied implementation."""
    await config_entry_factory()
    assert config_entry.state is ConfigEntryState.LOADED
    assert type(config_entry.runtime_data) is UnifiHub
    assert type(config_entry.runtime_data.connection).__module__.startswith(
        "homeassistant.components.unifi."
    )
    assert type(config_entry.runtime_data.entity_loader).__module__.startswith(
        "homeassistant.components.unifi."
    )


async def test_install_is_idempotent_and_restores_native_methods(
    hass: HomeAssistant,
) -> None:
    """Reloading the adapter cannot stack filter wrappers or invert controls twice."""
    loader_module = importlib.import_module(
        "homeassistant.components.unifi.hub.entity_loader"
    )
    original = loader_module.UnifiEntityLoader._should_add_entity
    native = await async_prepare(hass)
    first_wrapper = loader_module.UnifiEntityLoader._should_add_entity
    assert first_wrapper is not original
    install_patches(native)
    assert loader_module.UnifiEntityLoader._should_add_entity is first_wrapper
    uninstall_patches()
    assert loader_module.UnifiEntityLoader._should_add_entity is original


async def test_changed_core_filter_contract_fails_before_api_or_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    aioclient_mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signature drift cannot silently expose all clients or contact the controller."""
    loader_module = importlib.import_module(
        "homeassistant.components.unifi.hub.entity_loader"
    )

    def incompatible_filter(self, description):
        return True

    monkeypatch.setattr(
        loader_module.UnifiEntityLoader, "_should_add_entity", incompatible_filter
    )
    with pytest.raises(ConfigEntryError, match="incompatible") as error:
        await async_prepare(hass)
    assert isinstance(error.value.__cause__, AdapterCompatibilityError)
    assert aioclient_mock.call_count == 0
    assert not any(
        entry.config_entry_id == config_entry.entry_id
        for entry in entity_registry.entities.values()
    )


async def test_incompatible_core_cannot_start_config_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    aioclient_mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real config-entry path rejects incompatibility instead of loading unfiltered."""
    sensor_module = importlib.import_module("homeassistant.components.unifi.sensor")

    def incompatible_discovery(hub):
        return True

    monkeypatch.setattr(
        sensor_module, "_async_should_add_sensor", incompatible_discovery
    )
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert (
        ir.async_get(hass).async_get_issue("unifi", "adapter_incompatible") is not None
    )
    assert aioclient_mock.call_count == 0
    assert not any(
        entry.config_entry_id == config_entry.entry_id
        for entry in entity_registry.entities.values()
    )


def test_native_backend_is_not_vendored() -> None:
    """Production ships only policy/proxies, so a Core upgrade changes the backend."""
    root = Path(__file__).resolve().parents[1] / "unifi"
    assert not list((root / "hub").glob("*.py"))
    assert not (root / "coordinator.py").exists()
    assert not (root / "entity.py").exists()
