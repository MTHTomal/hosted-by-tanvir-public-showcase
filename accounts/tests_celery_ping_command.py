from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from kombu.exceptions import OperationalError as KombuOperationalError

from accounts.management.commands.celery_ping import (
    MASKED_PASSWORD,
    mask_broker_url,
    mask_broker_url_in_text,
)


class CeleryPingCommandTests(SimpleTestCase):
    def test_command_is_registered(self):
        self.assertIn("celery_ping", get_commands())

    @override_settings(
        CELERY_BROKER_URL="memory://",
        CELERY_RESULT_BACKEND=None,
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
    )
    def test_command_queues_ping_task_in_eager_mode(self):
        output = StringIO()

        call_command("celery_ping", stdout=output)

        text = output.getvalue()
        self.assertIn("Using broker: memory://", text)
        self.assertRegex(text, r"Queued ping task: \S+")
        self.assertIn("Check the Celery worker terminal for execution.", text)
        self.assertIn(
            "Windows worker command: python -m celery -A hosted_by_tanvir worker -l info -P solo",
            text,
        )

    def test_mask_broker_url_hides_password(self):
        masked = mask_broker_url("redis://:secret@localhost:6379/0")

        self.assertEqual(masked, f"redis://:{MASKED_PASSWORD}@localhost:6379/0")
        self.assertNotIn("secret", masked)

    def test_mask_broker_url_preserves_urls_without_password(self):
        self.assertEqual(
            mask_broker_url("redis://localhost:6379/0"),
            "redis://localhost:6379/0",
        )

    def test_mask_broker_url_in_text_hides_configured_broker_password(self):
        broker_url = "redis://:secret@localhost:6379/0"
        masked = mask_broker_url_in_text(f"could not connect to {broker_url}", broker_url)

        self.assertNotIn("secret", masked)
        self.assertIn(f"redis://:{MASKED_PASSWORD}@localhost:6379/0", masked)

    @override_settings(
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_RESULT_BACKEND=None,
    )
    def test_command_does_not_require_result_backend(self):
        output = StringIO()
        fake_get = Mock()
        fake_result = SimpleNamespace(id="demo-task-id", get=fake_get)

        with patch(
            "accounts.management.commands.celery_ping.ping_celery.delay",
            return_value=fake_result,
        ):
            call_command("celery_ping", stdout=output)

        self.assertIn("Queued ping task: demo-task-id", output.getvalue())
        fake_get.assert_not_called()

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_command_reports_broker_failure_cleanly(self):
        output = StringIO()

        with patch(
            "accounts.management.commands.celery_ping.ping_celery.delay",
            side_effect=KombuOperationalError("connection refused"),
        ):
            with self.assertRaisesMessage(CommandError, "Could not queue Celery ping task"):
                call_command("celery_ping", stdout=output)

        self.assertIn("Using broker: redis://localhost:6379/0", output.getvalue())
