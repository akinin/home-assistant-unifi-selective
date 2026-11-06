"""Structural and semantic contracts for the private UniFi extension points."""

from annotationlib import Format
from dataclasses import fields, is_dataclass
import inspect
from types import SimpleNamespace

from homeassistant.exceptions import ConfigEntryError

PREFIX = "homeassistant.components.unifi"
SUPPORTED_PLATFORMS = {
    "button",
    "device_tracker",
    "image",
    "light",
    "sensor",
    "switch",
    "update",
}


class AdapterCompatibilityError(ConfigEntryError):
    """The installed backend cannot safely apply the selected-client policy."""


def _require(condition, message):
    if not condition:
        raise AdapterCompatibilityError(message)


def _signature(target, expected, label):
    _require(callable(target), f"Missing callable: {label}")
    actual = tuple(
        inspect.signature(target, annotation_format=Format.STRING).parameters
    )
    _require(
        actual == expected, f"Changed signature {label}: {actual}, expected {expected}"
    )


def validate_native(modules):
    """Reject drift before any patch, controller connection or registry change."""

    def module(name=""):
        qualified = f"{PREFIX}.{name}" if name else PREFIX
        _require(qualified in modules, f"Native module missing: {qualified}")
        value = modules[qualified]
        _require(value.__name__ == qualified, f"Unexpected module origin: {qualified}")
        return value

    native = module()
    _require(
        {str(item) for item in native.PLATFORMS} == SUPPORTED_PLATFORMS,
        "UniFi platform set changed; new discovery paths require review",
    )
    config = module("hub.config").UnifiConfig
    hub = module("hub.hub").UnifiHub
    loader = module("hub.entity_loader").UnifiEntityLoader
    entity = module("entity")
    tracker = module("device_tracker").UnifiScannerEntity
    sensor = module("sensor")
    switch = module("switch")
    factory = inspect.getattr_static(config, "from_config_entry")
    listener = inspect.getattr_static(hub, "async_config_entry_updated")
    _require(
        isinstance(factory, classmethod), "UniFi config factory is not a classmethod"
    )
    _require(isinstance(listener, staticmethod), "UniFi options listener is not static")
    _signature(
        factory.__func__, ("cls", "config_entry"), "UnifiConfig.from_config_entry"
    )
    _signature(listener.__func__, ("hass", "config_entry"), "UnifiHub.options listener")
    _signature(hub.initialize, ("self",), "UnifiHub.initialize")
    _signature(
        loader._should_add_entity, ("self", "description", "obj_id"), "entity discovery"
    )
    _signature(
        loader._remove_client,
        ("self", "entity_registry", "device_registry", "entity_id", "mac"),
        "stale cleanup",
    )
    _signature(
        loader.get_data_update_coordinator, ("self", "handler"), "inventory coordinator"
    )
    _signature(
        sensor._async_should_add_sensor,
        ("hub", "description", "obj_id"),
        "sensor discovery",
    )
    _signature(tracker.async_initiate_state, ("self",), "tracker defaults")
    _signature(
        entity.UnifiEntity.async_signal_options_updated,
        ("self",),
        "entity option cleanup",
    )
    _signature(entity.UnifiEntity.remove_item, ("self", "keys"), "live entity removal")
    _require(
        is_dataclass(config), "Native UniFi configuration is no longer a dataclass"
    )
    expected_fields = {
        "entry",
        "option_track_clients",
        "option_track_devices",
        "option_supported_clients",
        "option_block_clients",
    }
    _require(
        expected_fields <= {field.name for field in fields(config)},
        "Native UniFi config fields changed",
    )
    for name in ("async_client_device_info_fn", "async_device_device_info_fn"):
        _require(
            callable(getattr(entity, name, None)), f"Missing device classifier: {name}"
        )
    blocks = [item for item in switch.ENTITY_DESCRIPTIONS if item.key == "Block client"]
    _require(
        len(blocks) == 1 and is_dataclass(blocks[0]), "Block-client descriptor changed"
    )
    block = blocks[0]
    _signature(block.control_fn, ("hub", "obj_id", "target"), "block control")
    _require(
        set(block.event_is_on or ()) == set(switch.CLIENT_UNBLOCKED),
        "Native block event semantics changed",
    )
    for blocked in (False, True):
        _require(
            block.is_on_fn(None, SimpleNamespace(blocked=blocked)) is (not blocked),
            "Native block state semantics changed",
        )
    _require(
        block.device_info_fn is entity.async_client_device_info_fn,
        "Block client bypasses classifier",
    )

    # A future descriptor must not introduce a client/physical discovery path
    # that bypasses the policy's helper-identity classification.
    class ApiShape:
        def __init__(self):
            self.handlers = {}

        def __getattr__(self, name):
            return self.handlers.setdefault(name, object())

    api = ApiShape()
    clients = api.clients
    physical = (api.devices, api.ports, api.outlets)
    for platform in SUPPORTED_PLATFORMS:
        for description in getattr(module(platform), "ENTITY_DESCRIPTIONS", ()):
            try:
                handler = description.api_handler_fn(api)
            except (AttributeError, TypeError) as err:
                raise AdapterCompatibilityError(
                    f"Changed handler mapping: {platform}/{description.key}"
                ) from err
            if handler is clients:
                _require(
                    description.device_info_fn is entity.async_client_device_info_fn,
                    f"Unclassified client descriptor: {platform}/{description.key}",
                )
            elif any(handler is value for value in physical):
                _require(
                    description.device_info_fn is entity.async_device_device_info_fn,
                    f"Unclassified physical descriptor: {platform}/{description.key}",
                )
    flow = module("config_flow")
    _require(
        hasattr(flow.UnifiFlowHandler, "async_get_options_flow"),
        "Native configuration flow changed",
    )
    _require(
        hasattr(flow.UnifiOptionsFlowHandler, "async_step_init"),
        "Native options flow changed",
    )
    constants = module("const")
    for name, expected in {
        "CONF_CLIENT_SOURCE": "client_source",
        "CONF_BLOCK_CLIENT": "block_client",
        "CONF_TRACK_CLIENTS": "track_clients",
        "CONF_TRACK_DEVICES": "track_devices",
    }.items():
        _require(
            getattr(constants, name, None) == expected,
            f"Native option key changed: {name}",
        )
