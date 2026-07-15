# test_settings.py

import os
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings, RequestFactory
from django.conf import settings
import hosted_by_tanvir.env as env_module
from hosted_by_tanvir.env import (
    initialize_environment,
    env_bool,
    env_first,
    env_list,
    resolve_allowed_hosts,
)


class TestSettingsOverride(SimpleTestCase):
    @override_settings(SECURE_SSL_REDIRECT=False, DEBUG=True)
    def test_override_settings(self):
        """
        Ensures that the settings override for tests is applied correctly.
        This prevents any production-level behaviors like HTTPS redirection
        during tests.
        """
        self.assertFalse(settings.SECURE_SSL_REDIRECT)
        self.assertTrue(settings.DEBUG)


class EnvHelperTests(SimpleTestCase):
    def test_env_bool_parses_expected_truthy_and_falsy_values(self):
        config = DummyConfig(
            {
                "TRUE_VALUE": "yes",
                "FALSE_VALUE": "off",
            }
        )

        self.assertIs(env_bool(config, "TRUE_VALUE", default=False), True)
        self.assertIs(env_bool(config, "FALSE_VALUE", default=True), False)

    def test_env_bool_falls_back_to_default_for_invalid_values(self):
        config = DummyConfig({"DEBUG": "release"})

        self.assertIs(env_bool(config, "DEBUG", default=False), False)
        self.assertIs(env_bool(config, "DEBUG", default=True), True)

    def test_env_list_trims_and_ignores_empty_items(self):
        config = DummyConfig({"ALLOWED_HOSTS": " localhost, ,example.com ,, 127.0.0.1 "})

        self.assertEqual(
            env_list(config, "ALLOWED_HOSTS"),
            ["localhost", "example.com", "127.0.0.1"],
        )

    def test_env_first_prefers_uppercase_discord_link(self):
        config = DummyConfig(
            {
                "DISCORD_LINK": "https://discord.gg/upper",
                "discord_link": "https://discord.gg/lower",
            }
        )

        self.assertEqual(
            env_first(config, "DISCORD_LINK", "discord_link", default=""),
            "https://discord.gg/upper",
        )

    def test_env_first_falls_back_to_lowercase_discord_link(self):
        config = DummyConfig(
            {
                "DISCORD_LINK": "   ",
                "discord_link": "https://discord.gg/lower",
            }
        )

        self.assertEqual(
            env_first(config, "DISCORD_LINK", "discord_link", default=""),
            "https://discord.gg/lower",
        )

    def test_resolve_allowed_hosts_requires_explicit_production_host(self):
        config = DummyConfig({})

        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "ALLOWED_HOSTS or RENDER_EXTERNAL_HOSTNAME must be set when DEBUG is False.",
        ):
            resolve_allowed_hosts(config, debug=False)

    def test_resolve_allowed_hosts_allows_explicit_production_hosts(self):
        config = DummyConfig({"ALLOWED_HOSTS": "example.com,www.example.com"})

        self.assertEqual(
            resolve_allowed_hosts(config, debug=False),
            ["example.com", "www.example.com"],
        )

    def test_resolve_allowed_hosts_includes_render_external_hostname(self):
        config = DummyConfig({"RENDER_EXTERNAL_HOSTNAME": "hosted-by-tanvir.onrender.com"})

        self.assertEqual(
            resolve_allowed_hosts(config, debug=False),
            ["hosted-by-tanvir.onrender.com"],
        )

    def test_resolve_allowed_hosts_keeps_local_debug_defaults(self):
        config = DummyConfig({})

        self.assertEqual(
            resolve_allowed_hosts(config, debug=True),
            ["localhost", "127.0.0.1"],
        )


class SiteLinksContextProcessorTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(DISCORD_LINK="https://discord.gg/example")
    def test_site_links_exposes_discord_link(self):
        from tournament.context_processors import site_links

        request = self.factory.get("/")

        self.assertEqual(
            site_links(request),
            {"discord_link": "https://discord.gg/example"},
        )

    @override_settings(DISCORD_LINK="")
    def test_site_links_allows_empty_discord_link(self):
        from tournament.context_processors import site_links

        request = self.factory.get("/")

        self.assertEqual(site_links(request), {"discord_link": ""})


class DummyConfig:
    def __init__(self, values):
        self.values = values

    def __call__(self, key, default=None):
        return self.values.get(key, default)


class EnvLoadingTests(SimpleTestCase):
    def test_initialize_environment_uses_env_local_override_when_process_value_absent(self):
        with patch.object(env_module, "_resolve_base_env_file", return_value=Path("base.env")):
            with patch.object(env_module, "_resolve_local_env_file", return_value=Path("base.env.local")):
                with patch.object(
                    env_module,
                    "_read_env_file",
                    side_effect=[
                        {"SHARED_KEY": "base", "BASE_ONLY": "1"},
                        {"SHARED_KEY": "local", "LOCAL_ONLY": "1"},
                    ],
                ):
                    with patch.dict(os.environ, {}, clear=False):
                        for key in ("ENV_FILE", "SHARED_KEY", "BASE_ONLY", "LOCAL_ONLY"):
                            os.environ.pop(key, None)

                        initialize_environment(force=True)

                        self.assertEqual(os.environ.get("SHARED_KEY"), "local")
                        self.assertEqual(os.environ.get("BASE_ONLY"), "1")
                        self.assertEqual(os.environ.get("LOCAL_ONLY"), "1")

    def test_initialize_environment_keeps_process_env_as_highest_precedence(self):
        with patch.object(env_module, "_resolve_base_env_file", return_value=Path("base.env")):
            with patch.object(env_module, "_resolve_local_env_file", return_value=Path("base.env.local")):
                with patch.object(
                    env_module,
                    "_read_env_file",
                    side_effect=[
                        {"SHARED_KEY": "base", "BASE_ONLY": "1"},
                        {"SHARED_KEY": "local", "LOCAL_ONLY": "1"},
                    ],
                ):
                    with patch.dict(os.environ, {}, clear=False):
                        for key in ("ENV_FILE", "SHARED_KEY", "BASE_ONLY", "LOCAL_ONLY"):
                            os.environ.pop(key, None)
                        os.environ["SHARED_KEY"] = "process"

                        initialize_environment(force=True)

                        self.assertEqual(os.environ.get("SHARED_KEY"), "process")
                        self.assertEqual(os.environ.get("BASE_ONLY"), "1")
                        self.assertEqual(os.environ.get("LOCAL_ONLY"), "1")
