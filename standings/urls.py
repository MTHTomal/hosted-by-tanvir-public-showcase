# standings/urls.py

from django.urls import path
from standings import views

app_name = "standings"

urlpatterns = [
    path("tournament/<int:tournament_pk>/top-scorers/", views.top_scorers, name="top_scorers"),
    path("tournament/<int:tournament_pk>/top-assists/", views.top_assists, name="top_assists"),
]
