# tournament/context_processors.py

from django.conf import settings


def pending_results(request):
    """
    Injects pending_result_count into every template context.
    Used by base.html to show the badge on the Result Queue nav link.
    Only runs the query if the user is a logged-in staff member.
    """
    if request.user.is_authenticated and request.user.is_staff:
        from .models import Result, Tournament

        count = Result.objects.filter(
            status=Result.PENDING,
        ).exclude(
            fixture__tournament__status=Tournament.ARCHIVED,
        ).count()
        return {"pending_result_count": count}
    return {"pending_result_count": 0}


def site_links(request):
    return {"discord_link": settings.DISCORD_LINK}


def notification_badge(request):
    if request.user.is_authenticated:
        from accounts.models import Notification

        return {
            "unread_notification_count": Notification.objects.filter(
                user=request.user,
                is_read=False,
            ).count()
        }
    return {"unread_notification_count": 0}
