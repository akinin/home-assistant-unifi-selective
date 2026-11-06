"""Behavior of the selective client override on the 2026.11 integration base."""

from custom_components.unifi.adapter_policy import (
    CLIENT_FILTER_ALL,
    CLIENT_FILTER_NONE,
    CLIENT_FILTER_SELECTED,
    CONF_CLIENT_FILTER_MODE,
    CONF_CREATE_NETWORK_ENTITIES,
    policy_from_options,
    safe_prune,
)
import probatio
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.unifi.const import (
    CONF_ALLOW_BANDWIDTH_SENSORS,
    CONF_ALLOW_UPTIME_SENSORS,
    CONF_BLOCK_CLIENT,
    CONF_CLIENT_SOURCE,
    CONF_DPI_RESTRICTIONS,
    CONF_MORE_OPTIONS,
    CONF_TRACK_CLIENTS,
    CONF_TRACK_DEVICES,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import ConfigEntryFactoryType

SELECTED_MAC = "00:00:00:00:00:01"
OTHER_MAC = "00:00:00:00:00:02"
NETWORK_MAC = "00:00:00:00:01:01"
CLIENTS = [
    {
        "mac": SELECTED_MAC,
        "hostname": "Selected",
        "is_wired": True,
        "wired_rate_mbps": 1000,
    },
    {"mac": OTHER_MAC, "hostname": "Other", "is_wired": True, "wired_rate_mbps": 1000},
]
DEVICE = {
    "mac": NETWORK_MAC,
    "name": "Network",
    "model": "US16P150",
    "type": "usw",
    "state": 1,
    "ip": "10.0.1.1",
    "board_rev": 3,
    "version": "4.0.42",
    "next_interval": 20,
}


def client_entities(registry: er.EntityRegistry, mac: str) -> list[er.RegistryEntry]:
    """Find this client's tracker, bandwidth, uptime and control entities."""
    return [entry for entry in registry.entities.values() if mac in entry.unique_id]


@pytest.mark.parametrize(
    "mode", [CLIENT_FILTER_ALL, CLIENT_FILTER_SELECTED, CLIENT_FILTER_NONE]
)
@pytest.mark.parametrize("client_payload", [CLIENTS])
async def test_filter_modes_create_only_allowed_clients(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
    mode: str,
) -> None:
    """All/selected/none applies to every client platform, including trackers."""
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: mode,
            CONF_ALLOW_BANDWIDTH_SENSORS: True,
            CONF_ALLOW_UPTIME_SENSORS: True,
            CONF_CLIENT_SOURCE: [SELECTED_MAC]
            if mode == CLIENT_FILTER_SELECTED
            else [],
        },
    )
    await config_entry_factory()
    assert config_entry.state is ConfigEntryState.LOADED
    assert bool(client_entities(entity_registry, SELECTED_MAC)) is (
        mode != CLIENT_FILTER_NONE
    )
    assert bool(client_entities(entity_registry, OTHER_MAC)) is (
        mode == CLIENT_FILTER_ALL
    )
    if mode == CLIENT_FILTER_SELECTED:
        tracker = next(
            e
            for e in client_entities(entity_registry, SELECTED_MAC)
            if e.domain == "device_tracker"
        )
        assert tracker.disabled_by is None
        assert hass.states.get(tracker.entity_id) is not None
        assert {e.domain for e in client_entities(entity_registry, SELECTED_MAC)} >= {
            "device_tracker",
            "sensor",
            "switch",
        }


@pytest.mark.parametrize("client_payload", [CLIENTS])
async def test_control_only_client_survives_none_mode(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """None mode retains a client's explicit access-control switch."""
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_NONE,
            CONF_BLOCK_CLIENT: [OTHER_MAC],
        },
    )
    await config_entry_factory()
    assert not client_entities(entity_registry, SELECTED_MAC)
    entries = client_entities(entity_registry, OTHER_MAC)
    assert [e.domain for e in entries] == ["switch"]
    assert hass.states.get(entries[0].entity_id).state == "off"


@pytest.mark.parametrize("device_payload", [[DEVICE]])
@pytest.mark.parametrize("enabled", [False, True])
async def test_network_equipment_toggle(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
    enabled: bool,
) -> None:
    """The equipment toggle gates registry devices and all equipment platforms."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_CREATE_NETWORK_ENTITIES: enabled}
    )
    await config_entry_factory()
    device = device_registry.async_get_device_by_connection(
        (dr.CONNECTION_NETWORK_MAC, NETWORK_MAC), config_entry.entry_id
    )
    assert (device is not None) is enabled
    if enabled:
        tracker = entity_registry.async_get("device_tracker.network")
        assert tracker is not None and tracker.disabled_by is None
        assert hass.states.get(tracker.entity_id) is not None
    else:
        assert not any(
            NETWORK_MAC in e.unique_id for e in entity_registry.entities.values()
        )


@pytest.mark.parametrize("client_payload", [CLIENTS])
async def test_pruning_keeps_shared_devices_and_other_integration_entities(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """Deselecting a client detaches UniFi ownership without deleting shared data."""
    other = MockConfigEntry(domain="test", entry_id="other-entry", data={})
    other.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=other.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, OTHER_MAC)},
    )
    unifi_device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, OTHER_MAC)},
    )
    foreign = entity_registry.async_get_or_create(
        "sensor", "test", "foreign", config_entry=other, device_id=device.id
    )
    unifi_tracker = entity_registry.async_get_or_create(
        "device_tracker",
        "unifi",
        f"site_id-{OTHER_MAC}",
        config_entry=config_entry,
        device_id=unifi_device.id,
    )
    unifi_sensor = entity_registry.async_get_or_create(
        "sensor",
        "unifi",
        f"rx-{OTHER_MAC}",
        config_entry=config_entry,
        device_id=unifi_device.id,
    )
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
            CONF_CLIENT_SOURCE: [SELECTED_MAC],
        },
    )
    await config_entry_factory()
    remaining = device_registry.async_get(device.id)
    assert remaining is not None and remaining.config_entry_id == other.entry_id
    assert entity_registry.async_get(foreign.entity_id) is not None
    assert entity_registry.async_get(unifi_tracker.entity_id) is None
    assert entity_registry.async_get(unifi_sensor.entity_id) is None


@pytest.mark.parametrize("client_payload", [CLIENTS])
@pytest.mark.parametrize("device_payload", [[DEVICE]])
async def test_pruning_does_not_remove_equipment_or_unknown_mac_devices(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """Only positively identified clients are pruned by the client filter."""
    unknown = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "00:00:00:00:ff:ff")},
    )
    entity_registry.async_get_or_create(
        "sensor",
        "unifi",
        "future-sensor",
        config_entry=config_entry,
        device_id=unknown.id,
    )
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_NONE}
    )
    await config_entry_factory()
    assert device_registry.async_get(unknown.id) is not None
    assert (
        device_registry.async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, NETWORK_MAC), config_entry.entry_id
        )
        is not None
    )


@pytest.mark.parametrize(
    ("options", "mode", "equipment"),
    [
        ({}, CLIENT_FILTER_ALL, True),
        (
            {CONF_TRACK_CLIENTS: False, CONF_CLIENT_SOURCE: [SELECTED_MAC]},
            CLIENT_FILTER_SELECTED,
            True,
        ),
        ({CONF_TRACK_CLIENTS: False}, CLIENT_FILTER_NONE, True),
        ({CONF_TRACK_DEVICES: False}, CLIENT_FILTER_ALL, False),
    ],
)
async def test_legacy_option_migration(
    config_entry: MockConfigEntry,
    options: dict,
    mode: str,
    equipment: bool,
    hass: HomeAssistant,
) -> None:
    """Legacy options retain their meaning and client selections."""
    hass.config_entries.async_update_entry(config_entry, options=options)
    policy = policy_from_options(options)
    assert policy.mode == mode
    assert policy.network is equipment
    assert policy.selected == frozenset(options.get(CONF_CLIENT_SOURCE, []))


@pytest.mark.parametrize("client_payload", [CLIENTS])
@pytest.mark.parametrize(
    "mode", [CLIENT_FILTER_ALL, CLIENT_FILTER_SELECTED, CLIENT_FILTER_NONE]
)
async def test_option_form_stores_mode_and_control_selection(
    hass: HomeAssistant,
    config_entry_setup: MockConfigEntry,
    mode: str,
) -> None:
    """The probatio form saves modes, selections and compatibility flags."""
    result = await hass.config_entries.options.async_init(config_entry_setup.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_CLIENT_FILTER_MODE: mode,
            CONF_CLIENT_SOURCE: [SELECTED_MAC],
            CONF_CREATE_NETWORK_ENTITIES: False,
            CONF_BLOCK_CLIENT: [OTHER_MAC],
            CONF_MORE_OPTIONS: {},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CLIENT_FILTER_MODE] == mode
    assert result["data"][CONF_CLIENT_SOURCE] == (
        [] if mode == CLIENT_FILTER_NONE else [SELECTED_MAC]
    )
    assert result["data"][CONF_BLOCK_CLIENT] == [OTHER_MAC]
    assert result["data"][CONF_TRACK_CLIENTS] is (mode == CLIENT_FILTER_ALL)
    assert result["data"][CONF_TRACK_DEVICES] is False


@pytest.mark.parametrize("client_payload", [CLIENTS])
async def test_new_native_options_survive_and_input_is_not_mutated(
    hass: HomeAssistant,
    config_entry_setup: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Future native options pass through both the form and saving path."""
    from homeassistant.components.unifi.config_flow import (
        UnifiOptionsFlowHandler as NativeOptionsFlowHandler,
    )

    original = NativeOptionsFlowHandler.async_step_init

    async def future_options(flow, user_input=None):
        result = await original(flow, user_input)
        if schema := result.get("data_schema"):
            schema.schema[probatio.Optional("future_native", default=True)] = bool
        return result

    monkeypatch.setattr(NativeOptionsFlowHandler, "async_step_init", future_options)
    result = await hass.config_entries.options.async_init(config_entry_setup.entry_id)
    assert "future_native" in {
        getattr(key, "schema", key) for key in result["data_schema"].schema
    }
    user_input = {
        CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
        CONF_CLIENT_SOURCE: [SELECTED_MAC],
        CONF_CREATE_NETWORK_ENTITIES: True,
        CONF_BLOCK_CLIENT: [],
        CONF_MORE_OPTIONS: {},
        "future_native": False,
    }
    submitted = {**user_input, CONF_MORE_OPTIONS: dict(user_input[CONF_MORE_OPTIONS])}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=user_input
    )
    assert result["data"]["future_native"] is False
    assert user_input == submitted


@pytest.mark.parametrize("client_payload", [[CLIENTS[0]]])
@pytest.mark.parametrize("config_entry_options", [{CONF_BLOCK_CLIENT: [OTHER_MAC]}])
async def test_unavailable_control_client_is_kept_in_options(
    hass: HomeAssistant,
    config_entry_setup: MockConfigEntry,
) -> None:
    """Opening options cannot silently drop a currently unavailable control client."""
    result = await hass.config_entries.options.async_init(config_entry_setup.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_ALL,
            CONF_CLIENT_SOURCE: [],
            CONF_CREATE_NETWORK_ENTITIES: True,
            CONF_BLOCK_CLIENT: [OTHER_MAC],
            CONF_MORE_OPTIONS: {},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BLOCK_CLIENT] == [OTHER_MAC]


@pytest.mark.parametrize("client_payload", [CLIENTS])
async def test_block_switch_on_blocks_and_off_unblocks(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
    aioclient_mock,
) -> None:
    """User-facing Block access keeps its intentionally reversed upstream semantics."""
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
            CONF_CLIENT_SOURCE: [SELECTED_MAC],
        },
    )
    await config_entry_factory()
    entity = next(
        e
        for e in client_entities(entity_registry, SELECTED_MAC)
        if e.domain == "switch"
    )
    assert hass.states.get(entity.entity_id).state == "off"
    aioclient_mock.clear_requests()
    aioclient_mock.post("https://1.2.3.4:1234/api/s/site_id/cmd/stamgr")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity.entity_id}, blocking=True
    )
    assert aioclient_mock.mock_calls[0][2] == {"mac": SELECTED_MAC, "cmd": "block-sta"}
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity.entity_id}, blocking=True
    )
    assert aioclient_mock.mock_calls[1][2] == {
        "mac": SELECTED_MAC,
        "cmd": "unblock-sta",
    }


@pytest.mark.parametrize("client_payload", [[CLIENTS[0]]])
async def test_new_unselected_clients_do_not_reappear(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
    mock_websocket_message,
) -> None:
    """A later controller update cannot bypass the client filter."""
    from aiounifi.models.message import MessageKey

    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
            CONF_CLIENT_SOURCE: [SELECTED_MAC],
        },
    )
    await config_entry_factory()
    mock_websocket_message(message=MessageKey.CLIENT, data=CLIENTS[1])
    await hass.async_block_till_done()
    assert not client_entities(entity_registry, OTHER_MAC)
    assert (
        device_registry.async_get_device_by_connection(
            (dr.CONNECTION_NETWORK_MAC, OTHER_MAC), config_entry.entry_id
        )
        is None
    )


@pytest.mark.parametrize("client_payload", [[CLIENTS[0]]])
async def test_selected_tracker_keeps_user_disabled_setting(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """Selected clients keep explicit user tracker disables."""
    tracker = entity_registry.async_get_or_create(
        "device_tracker",
        "unifi",
        f"site_id-{SELECTED_MAC}",
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
            CONF_CLIENT_SOURCE: [SELECTED_MAC],
        },
    )
    await config_entry_factory()
    assert (
        entity_registry.async_get(tracker.entity_id).disabled_by
        is er.RegistryEntryDisabler.USER
    )
    assert hass.states.get(tracker.entity_id) is None


@pytest.mark.parametrize("client_payload", [CLIENTS])
async def test_failed_equipment_inventory_never_prunes_clients(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """A failed inventory refresh cannot turn a filtering pass into data loss."""
    await config_entry_factory()
    before = {entry.entity_id for entry in client_entities(entity_registry, OTHER_MAC)}
    assert before
    hub = config_entry.runtime_data
    from custom_components.unifi.adapter_policy import ClientPolicy

    hub._unifi_selective_policy = ClientPolicy(mode=CLIENT_FILTER_NONE)
    coordinator = hub.entity_loader.get_data_update_coordinator(hub.api.devices)
    coordinator.last_update_success = False
    safe_prune(hub)
    assert before <= set(entity_registry.entities)


@pytest.mark.parametrize("client_payload", [CLIENTS])
async def test_filter_reload_removes_live_statistics_when_inventory_fails(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Excluded live statistics cannot remain restored when cleanup defers on error."""
    from aiounifi.interfaces.devices import Devices

    from homeassistant.components.unifi.coordinator import UnifiDataUpdateCoordinator
    from homeassistant.helpers.update_coordinator import UpdateFailed

    options = {
        CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
        CONF_CLIENT_SOURCE: [SELECTED_MAC],
        CONF_ALLOW_BANDWIDTH_SENSORS: True,
        CONF_ALLOW_UPTIME_SENSORS: True,
    }
    hass.config_entries.async_update_entry(config_entry, options=options)
    await config_entry_factory()
    old_entities = client_entities(entity_registry, SELECTED_MAC)
    live_entities = [
        entry for entry in old_entities if hass.states.get(entry.entity_id) is not None
    ]
    assert any(entry.domain == "sensor" for entry in live_entities)
    original_update = UnifiDataUpdateCoordinator._async_update_data

    async def unavailable_inventory(coordinator):
        if isinstance(coordinator.handler, Devices):
            raise UpdateFailed("Mocked unavailable device inventory")
        return await original_update(coordinator)

    monkeypatch.setattr(
        UnifiDataUpdateCoordinator, "_async_update_data", unavailable_inventory
    )
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            **options,
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_NONE,
            CONF_CLIENT_SOURCE: [],
        },
    )
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert not any(
        entity_registry.async_get(entry.entity_id) for entry in live_entities
    )
    assert not any(hass.states.get(entry.entity_id) for entry in live_entities)
    # Disabled records may remain until a healthy inventory permits device pruning.
    assert all(
        entry.disabled_by is not None
        for entry in client_entities(entity_registry, SELECTED_MAC)
    )


@pytest.mark.parametrize("client_payload", [CLIENTS])
async def test_changing_client_selection_reloads_and_applies_filter(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """Changing the selected set reloads the hub and applies the new registry filter."""
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
            CONF_CLIENT_SOURCE: [SELECTED_MAC],
        },
    )
    await config_entry_factory()
    original_hub = config_entry.runtime_data
    assert client_entities(entity_registry, SELECTED_MAC)
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
            CONF_CLIENT_SOURCE: [OTHER_MAC],
        },
    )
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data is not original_hub
    assert not client_entities(entity_registry, SELECTED_MAC)
    assert client_entities(entity_registry, OTHER_MAC)


@pytest.mark.parametrize("client_payload", [CLIENTS])
@pytest.mark.parametrize(
    "dpi_group_payload",
    [
        [
            {
                "_id": "media-group",
                "name": "Media",
                "dpiapp_ids": ["media-app"],
            }
        ]
    ],
)
@pytest.mark.parametrize(
    "dpi_app_payload",
    [
        [
            {
                "_id": "media-app",
                "enabled": True,
                "blocked": True,
                "apps": [],
                "cats": [],
            }
        ]
    ],
)
async def test_disabling_dpi_during_filter_reload_removes_registry_entity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """Feature cleanup must run even when filter changes also require a full reload."""
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_SELECTED,
            CONF_CLIENT_SOURCE: [SELECTED_MAC],
        },
    )
    await config_entry_factory()
    dpi_entry = entity_registry.async_get_entity_id("switch", "unifi", "media-group")
    assert dpi_entry is not None
    assert hass.states.get(dpi_entry) is not None
    hass.config_entries.async_update_entry(
        config_entry,
        options={
            CONF_CLIENT_FILTER_MODE: CLIENT_FILTER_NONE,
            CONF_DPI_RESTRICTIONS: False,
        },
    )
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert entity_registry.async_get(dpi_entry) is None
    assert hass.states.get(dpi_entry) is None


@pytest.mark.parametrize(
    "clients_all_payload",
    [
        [
            {
                "mac": OTHER_MAC,
                "hostname": "Old",
                "is_wired": True,
                "last_seen": 1,
            }
        ]
    ],
)
async def test_stale_tracker_without_device_link_is_pruned(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    config_entry: MockConfigEntry,
    config_entry_factory: ConfigEntryFactoryType,
) -> None:
    """Legacy trackers without device_id are removed with their stale MAC device."""
    tracker = entity_registry.async_get_or_create(
        "device_tracker",
        "unifi",
        f"site_id-{OTHER_MAC}",
        config_entry=config_entry,
    )
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, OTHER_MAC)},
    )
    await config_entry_factory()
    assert config_entry.state is ConfigEntryState.LOADED
    assert entity_registry.async_get(tracker.entity_id) is None
    assert device_registry.async_get(device.id) is None
