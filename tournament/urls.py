# tournament/urls.py

from django.urls import path
from . import views

app_name = "tournament"

urlpatterns = [
    path("", views.home, name="home"),
    path("announcements/", views.announcement_list, name="announcement_list"),
    path("staff/", views.staff_dashboard, name="staff_dashboard"),
    path("staff/exports/", views.staff_export_dashboard, name="staff_export_dashboard"),
    path("staff/exports/results.csv", views.staff_export_results_csv, name="staff_export_results_csv"),
    path("staff/exports/player-stats.csv", views.staff_export_player_stats_csv, name="staff_export_player_stats_csv"),
    path("staff/exports/team-stats.csv", views.staff_export_team_stats_csv, name="staff_export_team_stats_csv"),
    path("staff/exports/head-to-head.csv", views.staff_export_head_to_head_csv, name="staff_export_head_to_head_csv"),
    path("staff/exports/prediction-dataset.zip", views.staff_export_prediction_dataset_zip, name="staff_export_prediction_dataset_zip"),
    path("staff/announcements/", views.staff_announcement_list, name="staff_announcement_list"),
    path("staff/announcements/create/", views.staff_announcement_create, name="staff_announcement_create"),
    path("staff/announcements/<int:pk>/edit/", views.staff_announcement_edit, name="staff_announcement_edit"),
    path("staff/tournaments/", views.staff_tournament_list, name="staff_tournament_list"),
    path("staff/tournaments/create/", views.staff_tournament_create, name="staff_tournament_create"),
    path("staff/tournaments/<int:pk>/edit/", views.staff_tournament_edit, name="staff_tournament_edit"),
    path("staff/tournaments/<int:pk>/archive/", views.staff_tournament_archive, name="staff_tournament_archive"),
    path("staff/tournaments/<int:pk>/registrations/", views.staff_tournament_registrations, name="staff_tournament_registrations"),
    path("staff/tournaments/<int:pk>/registrations/add/", views.staff_tournament_registration_add, name="staff_tournament_registration_add"),
    path("staff/tournaments/<int:pk>/registrations/<int:registration_pk>/update/", views.staff_tournament_registration_update, name="staff_tournament_registration_update"),
    path("staff/tournaments/<int:pk>/registrations/groups/bulk/", views.staff_tournament_group_assignment_bulk_update, name="staff_tournament_group_assignment_bulk_update"),
    path("staff/tournaments/<int:pk>/registrations/<int:registration_pk>/group/", views.staff_tournament_group_assignment_update, name="staff_tournament_group_assignment_update"),
    path("staff/tournaments/<int:pk>/generate-fixtures/", views.staff_generate_fixtures, name="staff_generate_fixtures"),
    path("staff/results/", views.staff_pending_results, name="staff_pending_results"),
    path("staff/complaints/", views.staff_complaint_list, name="staff_complaint_list"),
    path("staff/complaints/<int:pk>/", views.staff_complaint_detail, name="staff_complaint_detail"),
    path("complaints/", views.complaint_list, name="complaint_list"),
    path("complaints/new/", views.complaint_create, name="complaint_create"),
    path("complaints/<int:pk>/", views.complaint_detail, name="complaint_detail"),
    path("tournaments/", views.tournament_list, name="tournament_list"),
    path("tournament/<int:pk>/", views.tournament_detail, name="tournament_detail"),
    path("tournament/<int:pk>/register/", views.tournament_register, name="tournament_register"),
    path("fixture/<int:pk>/", views.fixture_detail, name="fixture_detail"),
    path("fixture/<int:pk>/schedule/", views.staff_fixture_schedule_update, name="staff_fixture_schedule_update"),
    path("fixture/<int:fixture_pk>/submit/", views.result_submit, name="result_submit"),
    path("result/<int:pk>/opponent-response/", views.result_opponent_response, name="result_opponent_response"),
    path("result/<int:pk>/player-stats/", views.result_edit, name="result_player_stats_edit"),
    path("queue/result/<int:pk>/edit/", views.result_edit, name="result_edit"),
    path("fixture/<int:fixture_pk>/pending/", views.result_pending, name="result_pending"),
    # Admin result queue
    path("queue/", views.admin_result_queue, name="admin_queue"),
    path("queue/result/<int:pk>/approve/", views.result_approve, name="result_approve"),
    path("queue/result/<int:pk>/reject/", views.result_reject, name="result_reject"),
    path("queue/result/<int:pk>/dispute/", views.result_dispute, name="result_dispute"),
    path("tournament/<int:tournament_pk>/standings-partial/", views.standings_partial, name="standings_partial"),
]
