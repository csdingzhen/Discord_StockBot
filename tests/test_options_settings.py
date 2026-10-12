"""No environment secrets or live APIs are needed to validate tracked settings."""
import copy
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.options_settings import DEFAULT_PATH, load_options_settings


class OptionsSettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_options_settings()

    def load(self, value):
        with patch.object(Path, "open", return_value=io.StringIO(json.dumps(value))):
            return load_options_settings()

    def test_tracked_defaults_and_file_location(self):
        self.assertTrue(DEFAULT_PATH.is_absolute())
        self.assertEqual(self.settings["scan"]["universe"], "market")
        self.assertEqual(self.settings["scan"]["watchlist"], [])
        self.assertEqual(self.settings["moomoo"]["transport"], "rest")
        self.assertIsInstance(self.settings["moomoo"]["enabled"], bool)
        self.assertIsInstance(self.settings["catalyst"]["enabled"], bool)
        self.assertTrue({"QQQ", "SPY", "SPX", "IWM", "TQQQ"}.issubset(
            self.settings["scan"]["excluded_underlyings"]))

    def test_legacy_environment_cannot_silently_override_file(self):
        with patch.dict(os.environ, {"OPTIONS_MIN_PREMIUM": "1", "MOOMOO_MAX_PAGES": "9999",
                                     "MOOMOO_ENABLED": "true", "CATALYST_ENABLED": "true"}):
            self.assertEqual(load_options_settings(), self.settings)

    def test_types_ranges_unknown_and_missing_keys_rejected(self):
        cases = [("scan", "min_premium", -1), ("scan", "l2_score", True),
                 ("scan", "l3_score", 101), ("scan", "min_iv_jump", float("nan")),
                 ("moomoo", "enabled", "false"), ("moomoo", "max_pages", 0),
                 ("catalyst", "search_provider", "unknown"), ("catalyst", "max_concurrency", 50),
                 ("scan", "watchlist", ["aapl"]), ("scan", "min_premuim", 100)]
        for section, key, value in cases:
            with self.subTest(key=key, value=value):
                settings = copy.deepcopy(self.settings)
                settings[section][key] = value
                with self.assertRaises(ValueError):
                    self.load(settings)
        del self.settings["scan"]["min_premium"]
        with self.assertRaisesRegex(ValueError, "missing keys"):
            self.load(self.settings)

    def test_inconsistent_scope_and_thresholds_rejected(self):
        for section, key, value in (("scan", "l2_score", 90), ("scan", "short_dte", 60),
                                    ("moomoo", "transport", "opend"), ("scan", "universe", "watchlist")):
            settings = copy.deepcopy(self.settings)
            settings["scan"].update(l2_score=35, l3_score=65, short_dte=14, max_dte=45,
                                     universe="market", watchlist=[])
            settings["moomoo"]["transport"] = "rest"
            settings[section][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load(settings)

    def test_valid_custom_weights_and_context_are_objects_not_json_strings(self):
        self.settings["scan"]["score_weights"]["premium_activity"] = 0
        self.settings["catalyst"]["company_context"] = {"HUM": {"aliases": ["Humana"], "topics": ["CMS"]}}
        self.assertEqual(self.load(self.settings), self.settings)
        self.settings["catalyst"]["company_context"]["HUM"]["topics"] = [True]
        with self.assertRaises(ValueError):
            self.load(self.settings)

    def test_malformed_duplicate_and_missing_file_fail_loudly(self):
        for raw in ('{"version":1,"version":1}', '{"bad":'):
            with patch.object(Path, "open", return_value=io.StringIO(raw)), self.assertRaises(ValueError):
                load_options_settings()
        with patch.object(Path, "open", side_effect=FileNotFoundError()), self.assertRaises(ValueError):
            load_options_settings()
