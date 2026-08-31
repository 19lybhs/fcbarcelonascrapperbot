"""
Fuente de datos: ESPN API JSON (fuente primaria).

Usa la API informal de ESPN para obtener el calendario y resultados
del FC Barcelona. No requiere autenticación ni cloudscraper.

Endpoint principal:
    https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/teams/83/schedule
"""

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# ID del FC Barcelona en ESPN
BARCA_TEAM_ID = "83"

# Ligas que cubre el scraper (se consultan todas)
LEAGUES = {
    "esp.1": "La Liga",
    "uefa.champions": "Champions League",
    "esp.copa_del_rey": "Copa del Rey",
    "esp.super_cup": "Supercopa de España",
    "uefa.super_cup": "Supercopa de Europa",
    "fifa.cwc": "Mundial de Clubes",
}

# Mapeo de estados ESPN → estados internos para Flutter
STATUS_MAP = {
    "STATUS_SCHEDULED": "scheduled",
    "STATUS_IN_PROGRESS": "live",
    "STATUS_HALFTIME": "live",
    "STATUS_FULL_TIME": "finished",
    "STATUS_FINAL": "finished",
    "STATUS_FINAL_PENALTY": "finished",
    "STATUS_POSTPONED": "postponed",
    "STATUS_CANCELED": "canceled",
    "STATUS_SUSPENDED": "suspended",
    "STATUS_DELAYED": "postponed",
    "STATUS_FIRST_HALF": "live",
    "STATUS_SECOND_HALF": "live",
    "STATUS_EXTRA_TIME": "live",
    "STATUS_PENALTIES": "live",
}

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
REQUEST_TIMEOUT = 30  # segundos


def fetch_schedule(season: int | None = None) -> list[dict[str, Any]]:
    """
    Obtiene todos los partidos del Barça de todas las ligas configuradas.

    Args:
        season: Año de la temporada (ej. 2026 para 2026-27). Si es None,
                se calcula la temporada actual automáticamente.

    Returns:
        Lista de partidos normalizados con la estructura estándar.
    """
    all_matches: list[dict[str, Any]] = []

    # Determinar la temporada actual
    if season is None:
        now = datetime.now(timezone.utc)
        # La temporada de fútbol comienza en agosto
        season = now.year if now.month >= 8 else now.year - 1

    for league_slug, league_name in LEAGUES.items():
        try:
            matches = _fetch_league_schedule(league_slug, league_name, season)
            all_matches.extend(matches)
            if matches:
                logger.info(
                    "ESPN [%s %d-%d]: %d partidos obtenidos",
                    league_name, season, season + 1, len(matches),
                )
        except Exception:
            logger.exception(
                "Error obteniendo datos de ESPN para %s temporada %d",
                league_name, season,
            )

    logger.info("ESPN total: %d partidos obtenidos", len(all_matches))
    return all_matches


def _fetch_league_schedule(
    league_slug: str,
    league_name: str,
    season: int | None = None,
) -> list[dict[str, Any]]:
    """Obtiene el calendario de una liga específica."""
    url = f"{BASE_URL}/{league_slug}/teams/{BARCA_TEAM_ID}/schedule"
    params = {"fixture": "true"}
    if season is not None:
        params["season"] = season

    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    events = data.get("events", [])
    matches = []

    for event in events:
        try:
            match = _parse_event(event, league_name)
            if match is not None:
                matches.append(match)
        except Exception:
            logger.exception(
                "Error parseando evento ESPN ID=%s", event.get("id", "?")
            )

    return matches


def _parse_event(event: dict, league_name: str) -> dict[str, Any] | None:
    """Parsea un evento de la API de ESPN al formato de la colección 'matches'."""
    competition_data = event.get("competitions", [{}])[0]
    competitors = competition_data.get("competitors", [])

    if len(competitors) < 2:
        return None

    # Identificar home y away
    home = None
    away = None
    for comp in competitors:
        if comp.get("homeAway") == "home":
            home = comp
        else:
            away = comp

    if home is None or away is None:
        return None

    # Datos de los equipos
    home_team = home.get("team", {})
    away_team = away.get("team", {})

    home_name = home_team.get("displayName", "")
    away_name = away_team.get("displayName", "")
    home_tla = home_team.get("abbreviation", "")
    away_tla = away_team.get("abbreviation", "")
    home_id = int(home_team.get("id", 0))
    away_id = int(away_team.get("id", 0))

    # Logos
    home_logos = home_team.get("logos", [])
    away_logos = away_team.get("logos", [])
    home_logo = home_logos[0]["href"] if home_logos else None
    away_logo = away_logos[0]["href"] if away_logos else None

    # ¿Es partido del Barça?
    is_barca = str(home_id) == BARCA_TEAM_ID or str(away_id) == BARCA_TEAM_ID

    # Goles (null si aún no se ha jugado)
    home_score = home.get("score", {})
    away_score = away.get("score", {})
    home_goals = home_score.get("value")
    away_goals = away_score.get("value")

    if home_goals is not None:
        home_goals = int(home_goals)
    if away_goals is not None:
        away_goals = int(away_goals)

    # Estado del partido
    status_info = competition_data.get("status", {})
    status_type = status_info.get("type", {})
    status_name = status_type.get("name", "STATUS_SCHEDULED")
    status = STATUS_MAP.get(status_name, "scheduled")

    if status == "scheduled":
        home_goals = None
        away_goals = None

    # Minuto actual (solo si en vivo)
    minute = None
    if status == "live":
        minute = status_info.get("displayClock", "")

    # Fecha → convertir a datetime de Madrid para Firestore Timestamp
    fecha_str = event.get("date", "")
    scheduled_at = None

    if fecha_str:
        try:
            dt_utc = datetime.fromisoformat(fecha_str.replace("Z", "+00:00"))
            madrid_tz = ZoneInfo("Europe/Madrid")
            scheduled_at = dt_utc.astimezone(madrid_tz)
        except (ValueError, TypeError):
            pass

    # Temporada
    season_info = event.get("season", {})
    temporada_raw = season_info.get("displayName", "")
    season = temporada_raw.split(" ")[0] if temporada_raw else ""

    season_str = str(season_info.get("year", ""))

    # Determinar si es local y asignar ID/TLA correctos
    is_home = (competitors[0]["homeAway"] == "home")
    home_team = competitors[0] if is_home else competitors[1]
    away_team = competitors[1] if is_home else competitors[0]

    competition_slug = LEAGUES.get(league_name, league_name).lower().replace(" ", "_")
    if competition_slug == "la_liga":
        competition_slug = "laliga"
    
    # Extraer el logo de la competición
    competition_logo = None
    league_info = event.get("league", {})
    alternate_id = league_info.get("alternateId")
    if alternate_id:
        competition_logo = f"https://a.espncdn.com/i/leaguelogos/soccer/500/{alternate_id}.png"

    def _get_team_logo(team_comp):
        logos = team_comp.get("team", {}).get("logos", [])
        return logos[0]["href"] if logos else None

    def _extract_stadium(ev):
        venue = ev.get("competitions", [{}])[0].get("venue", {})
        return venue.get("fullName", "")

    notes = event.get("competitions", [{}])[0].get("notes", [])

    match_data = {
        "homeTeam": home_team.get("team", {}).get("displayName", "Unknown"),
        "homeTeamId": int(home_team.get("id", 0)),
        "homeTeamTla": home_team.get("team", {}).get("abbreviation", ""),
        "homeTeamLogo": _get_team_logo(home_team),
        "homeGoals": home_goals,

        "awayTeam": away_team.get("team", {}).get("displayName", "Unknown"),
        "awayTeamId": int(away_team.get("id", 0)) if away_team.get("id") else 0,
        "awayTeamTla": away_team.get("team", {}).get("abbreviation", ""),
        "awayTeamLogo": _get_team_logo(away_team),
        "awayGoals": away_goals,

        "competition": competition_slug,
        "competitionLogo": competition_logo,
        "season": season_str,
        "round": _extract_round(notes, event),
        "status": status,
        "scheduledAt": scheduled_at,
        "stadium": _extract_stadium(event),
        "isBarcaMatch": True,
        "manualOverride": False,
        "espnEventId": event.get("id"),
        "processedAt": None,
    }
    
    if status == "scheduled":
        match_data["homeGoals"] = None
        match_data["awayGoals"] = None

    return match_data


def _competition_slug(name: str) -> str:
    """Convierte el nombre de competición de ESPN a slug para Firestore."""
    SLUG_MAP = {
        "Spanish LALIGA": "laliga",
        "LALIGA": "laliga",
        "Spanish Copa del Rey": "copa_del_rey",
        "UEFA Champions League": "champions_league",
        "Spanish Super Cup": "supercopa",
        "UEFA Super Cup": "supercopa_europa",
        "FIFA Club World Cup": "mundial_clubes",
    }
    return SLUG_MAP.get(name, name.lower().replace(" ", "_"))


def _extract_round(notes: list, event: dict) -> int | str | None:
    """Intenta extraer la jornada (int) o ronda eliminatoria (str) del partido."""
    import re

    # 1. Intentar desde notes (ej. "Matchday 3" o "Jornada 5")
    for note in notes:
        headline = note.get("headline", "")
        match = re.search(r"(?:Matchday|Jornada|Round|Week)\s*(\d+)", headline, re.IGNORECASE)
        if match:
            return int(match.group(1))

    # 2. Intentar sacar la fase eliminatoria desde seasonType.name
    season_type = event.get("seasonType", {})
    st_name = season_type.get("name", "")
    
    knockout_rounds = {
        "Final": "Final",
        "Semifinals": "Semifinales",
        "Quarterfinals": "Cuartos de Final",
        "Round of 16": "Octavos de Final",
        "Round of 32": "Dieciseisavos de Final",
    }
    if st_name in knockout_rounds:
        return knockout_rounds[st_name]

    # 3. Intentar desde el seasonType.week (como fallback)
    week = season_type.get("week", {})
    if isinstance(week, dict):
        num = week.get("number")
        if num is not None:
            return int(num)

    return None

