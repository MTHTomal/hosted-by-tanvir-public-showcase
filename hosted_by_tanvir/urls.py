# hosted_by_tanvir/urls.py

from django.contrib import admin
from django.urls import path, include
from accounts import views as account_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("marketplace/", account_views.marketplace, name="marketplace"),
    path("marketplace/my-availability/", account_views.marketplace_my_availability, name="marketplace_my_availability"),
    path("marketplace/team-recruiting/", account_views.marketplace_team_recruiting, name="marketplace_team_recruiting"),
    path("marketplace/invite/<int:player_id>/", account_views.marketplace_invite_player, name="marketplace_invite_player"),
    path("marketplace/invitations/", account_views.marketplace_invitations, name="marketplace_invitations"),
    path("marketplace/invitations/<int:invite_id>/accept/", account_views.marketplace_invitation_accept, name="marketplace_invitation_accept"),
    path("marketplace/invitations/<int:invite_id>/reject/", account_views.marketplace_invitation_reject, name="marketplace_invitation_reject"),
    path("staff/marketplace/", account_views.staff_marketplace, name="staff_marketplace"),
    path("staff/marketplace/assign/", account_views.staff_marketplace_assign, name="staff_marketplace_assign"),
    path("accounts/", include("accounts.urls", namespace="accounts")),
    path("standings/", include("standings.urls", namespace="standings")),
    path("", include("tournament.urls", namespace="tournament")),
]
