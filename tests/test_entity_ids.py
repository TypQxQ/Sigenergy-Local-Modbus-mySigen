"""Suggested entity IDs end in "_mysigen"; unique IDs never follow the name.

entity.py is loaded with small stand-ins for the Home Assistant pieces it
uses, so these run without Home Assistant installed.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import re
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/sigen_mysigen"


def _slugify(text: str) -> str:
    # Close enough to Home Assistant's slugify for the names used here.
    text = text.lower()
    for old, new in (("å", "a"), ("ä", "a"), ("ö", "o")):
        text = text.replace(old, new)
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


class _FakeEntity:
    """Stands in for CoordinatorEntity: the ID comes from the English name.

    Home Assistant builds the suggested object ID from the entity name in
    English (its object ID language) and puts the device name in front.
    """

    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, _item):
        return cls

    async def async_prepare_to_add_to_hass(self):
        return None

    @property
    def suggested_object_id(self):
        return self.english_name

    @property
    def translation_key(self):
        return self._attr_translation_key


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _load_entity_module():
    for name in ("homeassistant", "homeassistant.helpers", "homeassistant.util"):
        _module(name)
    _module(
        "homeassistant.helpers.device_registry",
        async_get=lambda hass: None,
        async_entries_for_config_entry=lambda registry, entry_id: [],
    )
    _module("homeassistant.helpers.update_coordinator", CoordinatorEntity=_FakeEntity)
    sys.modules["homeassistant.util"].slugify = _slugify
    package = _module("sigen_mysigen_ids_pkg")
    package.__path__ = []
    const = importlib.util.spec_from_file_location(
        "sigen_mysigen_ids_pkg.const", COMPONENT / "const.py"
    )
    const_module = importlib.util.module_from_spec(const)
    sys.modules[const.name] = const_module
    const.loader.exec_module(const_module)
    _module("sigen_mysigen_ids_pkg.coordinator", MySigenCoordinator=object)
    spec = importlib.util.spec_from_file_location(
        "sigen_mysigen_ids_pkg.entity", COMPONENT / "entity.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, const_module


entity, const = _load_entity_module()


def _english_names():
    data = json.loads((COMPONENT / "translations/en.json").read_text(encoding="utf-8"))
    return {
        (platform, key): value["name"]
        for platform, entities in data["entity"].items()
        for key, value in entities.items()
    }


def _swedish_translations():
    data = json.loads((COMPONENT / "translations/sv.json").read_text(encoding="utf-8"))
    return {
        f"component.{const.DOMAIN}.entity.{platform}.{key}.name": value["name"]
        for platform, entities in data["entity"].items()
        for key, value in entities.items()
    }


def _make(platform, key, english_name, translations=None, entry_id="01ENTRY"):
    coordinator = types.SimpleNamespace(
        hass=None,
        config_entry=types.SimpleNamespace(
            entry_id=entry_id, data={const.CONF_PLANT_ENTRY_ID: "01PLANT"}
        ),
        entity_name_translations=translations,
    )
    item = entity.MySigenEntity(coordinator, key)
    item.english_name = english_name
    item.platform_data = types.SimpleNamespace(domain=platform)
    asyncio.run(item.async_prepare_to_add_to_hass())
    return item


def _entity_id(platform, item, device="Sigen Plant"):
    # How Home Assistant joins the device name and the suggested object ID.
    return f"{platform}.{_slugify(f'{device} {item.suggested_object_id}')}"


class TestEntityIds(unittest.TestCase):
    def test_every_entity_id_ends_in_mysigen_with_automatic_language(self):
        for (platform, key), name in _english_names().items():
            item = _make(platform, key, name)
            entity_id = _entity_id(platform, item)
            self.assertTrue(entity_id.endswith("_mysigen"), entity_id)
            self.assertEqual(entity_id.count("mysigen"), 1, entity_id)

    def test_swedish_names_keep_the_english_entity_id(self):
        swedish = _swedish_translations()
        for (platform, key), name in _english_names().items():
            item = _make(platform, key, name, translations=swedish)
            self.assertEqual(
                item._attr_name,
                swedish[f"component.{const.DOMAIN}.entity.{platform}.{key}.name"],
            )
            self.assertEqual(
                _entity_id(platform, item), _entity_id(platform, _make(platform, key, name))
            )

    def test_examples(self):
        names = _english_names()
        mode = _make("sensor", "battery_override_mode", names[("sensor", "battery_override_mode")])
        self.assertEqual(
            _entity_id("sensor", mode), "sensor.sigen_plant_battery_override_mode_mysigen"
        )
        limit = _make(
            "number", "battery_override_power_limit",
            names[("number", "battery_override_power_limit")],
        )
        self.assertEqual(
            _entity_id("number", limit),
            "number.sigen_plant_battery_override_power_limit_mysigen",
        )

    def test_a_name_without_the_suffix_still_gets_it_once(self):
        self.assertEqual(entity.with_mysigen_suffix("Peak shaving"), "Peak shaving mySigen")
        self.assertEqual(
            entity.with_mysigen_suffix("Peak shaving (mySigen)"), "Peak shaving (mySigen)"
        )
        self.assertIsNone(entity.with_mysigen_suffix(None))

    def test_unique_id_does_not_follow_name_or_language(self):
        english = _make("sensor", "last_update", "Last update (mySigen)")
        swedish = _make(
            "sensor", "last_update", "Last update (mySigen)",
            translations=_swedish_translations(),
        )
        renamed = _make("sensor", "last_update", "Something else entirely")
        for item in (english, swedish, renamed):
            self.assertEqual(item._attr_unique_id, "01ENTRY_last_update")

    def test_no_entity_class_sets_its_own_suggested_id(self):
        # The base class is the one place that decides the ID, so the
        # power limit and every other entity follow the same rule.
        for path in COMPONENT.glob("*.py"):
            if path.name == "entity.py":
                continue
            self.assertNotIn("suggested_object_id", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
