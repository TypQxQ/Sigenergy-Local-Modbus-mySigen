"""Every language must carry exactly the English keys and placeholders."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

TRANSLATIONS = (
    Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen/translations"
)
# The same languages the Sigenergy Local Modbus integration ships.
LANGUAGES = ("da", "de", "en", "es", "fi", "fr", "it", "nb", "nl", "pl", "sv")


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}{key}.")
    else:
        yield prefix[:-1], node


def _load(language):
    return dict(_flatten(
        json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))
    ))


class TestTranslations(unittest.TestCase):
    def setUp(self):
        self.english = _load("en")

    def test_the_expected_languages_exist_and_no_others(self):
        found = sorted(path.stem for path in TRANSLATIONS.glob("*.json"))
        self.assertEqual(found, sorted(LANGUAGES))

    def test_every_language_has_exactly_the_english_keys(self):
        for language in LANGUAGES:
            other = _load(language)
            self.assertEqual(set(other) - set(self.english), set(), f"{language}: extra")
            self.assertEqual(set(self.english) - set(other), set(), f"{language}: missing")

    def test_placeholders_match_english(self):
        for language in LANGUAGES:
            for key, text in _load(language).items():
                self.assertEqual(
                    sorted(re.findall(r"\{\w+\}", text)),
                    sorted(re.findall(r"\{\w+\}", self.english[key])),
                    f"{language}: {key}",
                )

    def test_nothing_is_left_empty(self):
        for language in LANGUAGES:
            for key, text in _load(language).items():
                self.assertTrue(isinstance(text, str) and text.strip(), f"{language}: {key}")

    def test_every_entity_name_ends_with_the_mysigen_suffix_once(self):
        # The suffix keeps these apart from the Modbus entities on the same
        # device and gives the suggested entity ID its "_mysigen" ending.
        for language in LANGUAGES:
            for key, text in _load(language).items():
                if key.startswith("entity.") and key.endswith(".name"):
                    self.assertTrue(text.endswith(" (mySigen)"), f"{language}: {key}")
                    self.assertEqual(text.count("mySigen"), 1, f"{language}: {key}")

    def test_entity_names_stay_short(self):
        # Details such as "0 = no limit" belong in the usage guide, and a
        # button needs no "now".
        for language in LANGUAGES:
            for key, text in _load(language).items():
                if key.startswith("entity.") and key.endswith(".name"):
                    self.assertNotIn("=", text, f"{language}: {key}")
                    self.assertLessEqual(len(text), 50, f"{language}: {key}")
        for key in ("start_battery_override", "stop_battery_override"):
            name = self.english[f"entity.button.{key}.name"]
            self.assertNotIn(" now", name)

    def test_app_wording_is_kept_where_the_user_must_find_it(self):
        # "View and Edit" is the label in the mySigen app, whatever the
        # language of Home Assistant.
        key = "config.step.user.description"
        for language in LANGUAGES:
            self.assertIn("View and Edit", _load(language)[key], language)


if __name__ == "__main__":
    unittest.main()
