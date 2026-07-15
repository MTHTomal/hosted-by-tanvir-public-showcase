# standings/views.py

from django.shortcuts import render, get_object_or_404
from standings.models import PlayerStat
from tournament.models import Tournament


def _visible_tournaments_for(user):
    tournaments = Tournament.objects.all()
    if not user.is_authenticated or not user.is_staff:
        tournaments = tournaments.exclude(status=Tournament.DRAFT)
    return tournaments


def _leaderboard_rows(rows, total_key):
    return [
        {
            "player__username": row["player__username"],
            "player__in_game_name": row["player__in_game_name"],
            "player__id": row["player__id"],
            "total": row[total_key] or 0,
        }
        for row in rows
    ]


def top_scorers(request, tournament_pk):
    tournament = get_object_or_404(_visible_tournaments_for(request.user), pk=tournament_pk)
    scorers = PlayerStat.top_scorers(tournament, limit=20)

    return render(request, "standings/top_scorers.html", {
        "tournament": tournament,
        "top_scorers": scorers,
        "leaderboard_rows": _leaderboard_rows(scorers, "total_goals"),
    })


def top_assists(request, tournament_pk):
    tournament = get_object_or_404(_visible_tournaments_for(request.user), pk=tournament_pk)
    assists = PlayerStat.top_assists(tournament, limit=20)

    return render(request, "standings/top_assists.html", {
        "tournament": tournament,
        "top_assists": assists,
        "leaderboard_rows": _leaderboard_rows(assists, "total_assists"),
    })
