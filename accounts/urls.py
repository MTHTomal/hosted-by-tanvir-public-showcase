from django.urls import path, reverse_lazy
from django.contrib.auth.views import (
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView,
)
from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Password reset — built-in Django auth views
    path("password-reset/", PasswordResetView.as_view(success_url=reverse_lazy("accounts:password_reset_done")), name="password_reset"),
    path("password-reset/done/", PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("password-reset-confirm/<uidb64>/<token>/", PasswordResetConfirmView.as_view(success_url=reverse_lazy("accounts:password_reset_complete")), name="password_reset_confirm"),
    path("password-reset/complete/", PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("staff/teams/", views.staff_team_list, name="staff_team_list"),
    path("staff/teams/<int:pk>/", views.staff_team_detail, name="staff_team_detail"),
    path("staff/teams/<int:pk>/approve/", views.staff_team_approve, name="staff_team_approve"),
    path("staff/teams/<int:pk>/unapprove/", views.staff_team_unapprove, name="staff_team_unapprove"),
    path("staff/players/", views.staff_player_list, name="staff_player_list"),
    path("staff/players/<str:username>/edit/", views.staff_player_edit, name="staff_player_edit"),
    path("players/", views.player_list, name="player_list"),
    path("teams/", views.team_list, name="team_list"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("notifications/mark-all-read/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("profile/update/", views.update_profile, name="update_profile"),  # ← must be before profile/<username>/
    path("profile/<str:username>/", views.player_profile, name="profile"),
    path("team/<int:pk>/", views.team_detail, name="team_detail"),
    path("team/<int:pk>/edit/", views.team_edit, name="team_edit"),
]
