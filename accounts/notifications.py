from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from accounts.models import Notification


def _is_notifiable_user(user):
    return bool(
        user
        and getattr(user, "pk", None)
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
    )


def _clean_notification_payload(title, message, kind, url=""):
    title = (title or "").strip()
    message = (message or "").strip()
    kind = (kind or "").strip()
    url = (url or "").strip()
    if not title or not message or not kind:
        return None
    return title, message, kind, url


def notify_user(user, title, message, kind, url=""):
    if not _is_notifiable_user(user):
        return None

    payload = _clean_notification_payload(title, message, kind, url)
    if payload is None:
        return None
    title, message, kind, url = payload

    existing = Notification.objects.filter(
        user=user,
        title=title,
        message=message,
        kind=kind,
        url=url,
        is_read=False,
    ).first()
    if existing:
        return existing

    return Notification.objects.create(
        user=user,
        title=title,
        message=message,
        kind=kind,
        url=url,
    )


def notify_staff(title, message, kind, url=""):
    User = get_user_model()
    notifications = []
    for user in User.objects.filter(is_staff=True, is_active=True):
        notification = notify_user(
            user,
            title=title,
            message=message,
            kind=kind,
            url=url,
        )
        if notification is not None:
            notifications.append(notification)
    return notifications


def notify_user_with_optional_celery(user, title, message, kind, url=""):
    if not getattr(settings, "NOTIFICATIONS_USE_CELERY", False):
        return notify_user(user, title=title, message=message, kind=kind, url=url)

    if not _is_notifiable_user(user):
        return None

    payload = _clean_notification_payload(title, message, kind, url)
    if payload is None:
        return None
    user_id = user.pk

    def dispatch():
        from accounts.tasks import create_notification_task

        try:
            create_notification_task.delay(user_id, *payload)
        except Exception:
            User = get_user_model()
            notify_user(User.objects.filter(pk=user_id).first(), *payload)

    transaction.on_commit(dispatch)
    return None


def notify_staff_with_optional_celery(title, message, kind, url=""):
    if not getattr(settings, "NOTIFICATIONS_USE_CELERY", False):
        return notify_staff(title=title, message=message, kind=kind, url=url)

    payload = _clean_notification_payload(title, message, kind, url)
    if payload is None:
        return []

    def dispatch():
        from accounts.tasks import create_staff_notifications_task

        try:
            create_staff_notifications_task.delay(*payload)
        except Exception:
            notify_staff(*payload)

    transaction.on_commit(dispatch)
    return []
