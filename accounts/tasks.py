from celery import shared_task
from django.contrib.auth import get_user_model

from accounts.notifications import notify_user


@shared_task(name="accounts.create_notification_task")
def create_notification_task(user_id, title, message, kind, url=""):
    User = get_user_model()
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None

    notification = notify_user(
        user,
        title=title,
        message=message,
        kind=kind,
        url=url,
    )
    return notification.pk if notification is not None else None


@shared_task(name="accounts.create_staff_notifications_task")
def create_staff_notifications_task(title, message, kind, url=""):
    User = get_user_model()
    created_count = 0
    for user in User.objects.filter(is_staff=True, is_active=True):
        notification = notify_user(
            user,
            title=title,
            message=message,
            kind=kind,
            url=url,
        )
        if notification is not None:
            created_count += 1
    return created_count
