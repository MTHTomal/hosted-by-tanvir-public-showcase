from urllib.parse import urlsplit, urlunsplit

from celery.exceptions import CeleryError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from kombu.exceptions import OperationalError as KombuOperationalError

from hosted_by_tanvir.celery import ping_celery


MASKED_PASSWORD = "********"


def mask_broker_url(url):
    if not url:
        return "(not configured)"

    parts = str(url).split(";")
    return ";".join(_mask_single_broker_url(part) for part in parts)


def mask_broker_url_in_text(text, broker_url):
    broker_url = str(broker_url or "")
    if not broker_url:
        return str(text)

    return str(text).replace(broker_url, mask_broker_url(broker_url))


def _mask_single_broker_url(url):
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url

    if not parsed.netloc or "@" not in parsed.netloc:
        return url

    userinfo, hostinfo = parsed.netloc.rsplit("@", 1)
    if ":" not in userinfo:
        return url

    username, _password = userinfo.rsplit(":", 1)
    masked_netloc = f"{username}:{MASKED_PASSWORD}@{hostinfo}"
    return urlunsplit(parsed._replace(netloc=masked_netloc))


class Command(BaseCommand):
    help = "Queue the Celery ping task to validate the broker-to-worker loop."

    def handle(self, *args, **options):
        broker_url = getattr(settings, "CELERY_BROKER_URL", "")
        safe_broker_url = mask_broker_url(broker_url)

        self.stdout.write(f"Using broker: {safe_broker_url}")

        try:
            result = ping_celery.delay()
        except (KombuOperationalError, CeleryError, OSError, TimeoutError) as exc:
            safe_error = mask_broker_url_in_text(exc, broker_url)
            raise CommandError(
                "Could not queue Celery ping task. "
                "Make sure Redis is running and CELERY_BROKER_URL points to a reachable broker. "
                f"Broker: {safe_broker_url}. "
                f"Error: {safe_error}"
            ) from exc

        self.stdout.write(f"Queued ping task: {result.id or '<unknown>'}")
        self.stdout.write("Check the Celery worker terminal for execution.")
        self.stdout.write(
            "Windows worker command: python -m celery -A hosted_by_tanvir worker -l info -P solo"
        )
