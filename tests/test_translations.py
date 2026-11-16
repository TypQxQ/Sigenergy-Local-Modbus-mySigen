"""Translation files must match the English keys and placeholders."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

TRANSLATIONS = (
    Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen/translations"
)


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}{key}.")
    else:
        yield prefix[:-1], node


class TestTranslations(unittest.TestCase):
    def setUp(self):
        self.english = dict(_flatten(
            json.loads((TRANSLATIONS / "en.json").read_text(encoding="utf-8"))
        ))

    def test_other_languages_only_use_english_keys_and_placeholders(self):
        for path in TRANSLATIONS.glob("*.json"):
            if path.name == "en.json":
                continue
            other = dict(_flatten(json.loads(path.read_text(encoding="utf-8"))))
            self.assertFalse(set(other) - set(self.english), path.name)
            for key, text in other.items():
                self.assertEqual(
                    sorted(re.findall(r"\{\w+\}", text)),
                    sorted(re.findall(r"\{\w+\}", self.english[key])),
                    f"{path.name}: {key}",
                )

    def test_entity_names_stay_english(self):
        # Home Assistant builds entity IDs from the name in the system
        # language. Translating names would give new installs different IDs
        # than the documentation uses, so only states are translated.
        for path in TRANSLATIONS.glob("*.json"):
            if path.name == "en.json":
                continue
            other = dict(_flatten(json.loads(path.read_text(encoding="utf-8"))))
            names = [key for key in other if key.startswith("entity.") and key.endswith(".name")]
            self.assertEqual(names, [], path.name)

    def test_everything_but_entity_names_is_translated(self):
        expected = {
            key for key in self.english
            if not (key.startswith("entity.") and key.endswith(".name"))
        }
        for path in TRANSLATIONS.glob("*.json"):
            if path.name == "en.json":
                continue
            other = dict(_flatten(json.loads(path.read_text(encoding="utf-8"))))
            self.assertFalse(expected - set(other), path.name)


if __name__ == "__main__":
    unittest.main()
