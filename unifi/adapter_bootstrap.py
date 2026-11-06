"""Load installed Core requirements and validate hooks before native startup."""

import asyncio
import importlib
import logging
from pathlib import Path
import shutil
import sys

from homeassistant import components
from homeassistant.helpers import issue_registry as ir, translation
from homeassistant.loader import Integration
from homeassistant.requirements import async_process_requirements
from homeassistant.setup import async_setup_component

from .adapter_contracts import AdapterCompatibilityError, validate_native
from .adapter_metadata import build_metadata
from .adapter_patches import install_patches

LOGGER = logging.getLogger(__name__)
DOMAIN = "unifi"
DATA_KEY = "unifi_selective_adapter"
PREFIX = "homeassistant.components.unifi"
MODULES = (
    "",
    "const",
    "entity",
    "hub.config",
    "hub.hub",
    "hub.entity_loader",
    "device_tracker",
    "sensor",
    "switch",
    "config_flow",
    "diagnostics",
    "button",
    "image",
    "light",
    "update",
)


def native_attribute(module, name):
    """Expose native module attributes without substituting copied classes."""
    if name.startswith("__"):
        raise AttributeError(name)
    qualified = f"{PREFIX}.{module}" if module else PREFIX
    native = sys.modules.get(qualified)
    if native is None:
        raise AttributeError(name)
    return getattr(native, name)


def _load_modules():
    """Use fully qualified imports, avoiding HA's domain-only component cache."""
    return {
        f"{PREFIX}.{name}" if name else PREFIX: importlib.import_module(
            f"{PREFIX}.{name}" if name else PREFIX
        )
        for name in MODULES
    }


def _build_assets(native_dir):
    """Merge current native metadata with only the adapter's text changes."""
    target = Path(__file__).parent
    build_metadata(native_dir, target)
    for name in ("services.yaml",):
        if (source := native_dir / name).is_file():
            shutil.copyfile(source, target / name)


async def async_prepare(hass, config=None):
    """Validate and install once per HA instance; never fall back without filters."""
    state = hass.data.setdefault(DATA_KEY, {"lock": asyncio.Lock()})
    async with state["lock"]:
        if "modules" in state:
            return state["modules"]
        try:
            # This resolves only the native manifest, never async_get_component():
            # the latter is cached by domain and could recurse into this adapter.
            integration = await hass.async_add_executor_job(
                Integration.resolve_from_root, hass, components, DOMAIN
            )
            if integration is None or not integration.is_built_in:
                raise AdapterCompatibilityError("Built-in UniFi integration not found")
            await async_process_requirements(
                hass, DOMAIN, integration.requirements, is_built_in=True
            )
            for dependency in integration.dependencies:
                if not await async_setup_component(hass, dependency, config or {}):
                    raise AdapterCompatibilityError(f"Dependency failed: {dependency}")
            native = await hass.async_add_executor_job(_load_modules)
            validate_native(native)
            await hass.async_add_executor_job(_build_assets, integration.file_path)
            # Bootstrap may have loaded old generated translations in parallel.
            # First join that load, then invalidate using the public HA helper.
            await translation.async_load_integrations(hass, {DOMAIN})
            translation.async_invalidate_translations(hass, {DOMAIN})
            await translation.async_load_integrations(hass, {DOMAIN})
            install_patches(native)
        except (
            AdapterCompatibilityError,
            ImportError,
            AttributeError,
            TypeError,
            RuntimeError,
        ) as err:
            ir.async_create_issue(
                hass,
                DOMAIN,
                "adapter_incompatible",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="adapter_incompatible",
                translation_placeholders={"details": str(err)},
            )
            raise AdapterCompatibilityError(
                f"UniFi selective adapter is incompatible with this Core: {err}"
            ) from err
        state["modules"] = native
        ir.async_delete_issue(hass, DOMAIN, "adapter_incompatible")
        LOGGER.info(
            "Selective adapter prepared using built-in UniFi at %s",
            integration.file_path,
        )
        return native


async def async_native_module(hass, module=""):
    """Return the current native module after requirements/contracts are ready."""
    native = await async_prepare(hass)
    return native[f"{PREFIX}.{module}" if module else PREFIX]
