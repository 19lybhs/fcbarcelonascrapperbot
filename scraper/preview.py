"""Previa por equipo compartida con la ficha Flutter y el recordatorio."""
import logging
from datetime import datetime, timezone
from functools import lru_cache

import requests

from scraper.sources.espn import BASE_URL

log = logging.getLogger(__name__)


def session():
    @lru_cache(maxsize=48)
    def get(path):
        response = requests.get(BASE_URL + path, timeout=12)
        response.raise_for_status()
        return response.json()

    return get


def lineup(summary, team_id):
    side = next((r for r in summary.get("rosters", []) if str(r["team"]["id"]) == team_id), {})
    players = []
    for row in side.get("roster", []):
        if row.get("starter") is not True:
            continue
        athlete = row.get("athlete") or {}
        name = athlete.get("displayName") or athlete.get("fullName")
        if not name or not athlete.get("id"):
            return None
        place = str(row.get("formationPlace", ""))
        players.append({"id": str(athlete["id"]), "name": name,
                        "shortName": athlete.get("shortName") or name,
                        "place": int(place) if place.isdecimal() else 0,
                        "jersey": str(row.get("jersey") or ""),
                        "position": (row.get("position") or {}).get("abbreviation", "")})
    if len(players) != 11 or len({p["id"] for p in players}) != 11:
        return None
    formation = str(side.get("formation") or "")
    lines = formation.split("-")
    if (all(n.isdecimal() and 0 < int(n) < 10 for n in lines)
            and sum(map(int, lines)) == 10
            and {p["place"] for p in players} == set(range(1, 12))):
        players.sort(key=lambda p: p["place"])
    else:
        formation = ""
    return {"formation": formation, "players": players}


def score(team):
    raw = team.get("score")
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("displayValue"))
    value = str(raw).removesuffix(".0")
    return int(value) if value.isdecimal() else None


def recent_results(get, team_id, cutoff, excluded):
    year = cutoff.year - (cutoff.month < 8)
    events = {}
    for season in (year, year - 1):
        for event in get(f"/all/teams/{team_id}/schedule?season={season}")["events"]:
            game = event["competitions"][0]
            status = game["status"]["type"]
            eid = str(event["id"])
            if not status.get("completed") or eid == excluded:
                continue
            date = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            if date >= cutoff:
                continue
            teams = game["competitors"]
            ours = next((t for t in teams if str(t["team"]["id"]) == team_id), None)
            if not ours or len(teams) != 2:
                continue
            other = next(t for t in teams if t is not ours)
            gf, ga = score(ours), score(other)
            if gf is None or ga is None:
                continue
            outcome = "V" if gf > ga else "D" if gf < ga else "E"
            if gf == ga and status.get("name") == "STATUS_FINAL_PENALTY" and isinstance(ours.get("winner"), bool):
                outcome = "V" if ours["winner"] else "D"
            events[eid] = {"eventId": eid, "date": date.astimezone(timezone.utc).isoformat(),
                           "home": ours["homeAway"] == "home", "opponent": other["team"]["displayName"],
                           "gf": gf, "ga": ga, "outcome": outcome}
        if len(events) >= 5:
            break
    return sorted(events.values(), key=lambda g: g["date"], reverse=True)[:5]


def summary_for(get, league, event_id):
    summary = get(f"/{league}/summary?event={event_id}")
    if str(summary["header"]["competitions"][0]["id"]) != event_id:
        raise ValueError("El resumen pertenece a otro encuentro")
    return summary


def build(match, get, now):
    eid = str(match["espnEventId"])
    summary = summary_for(get, match.get("espnLeague") or "all", eid)
    teams = {}
    for side in ("home", "away"):
        tid = str(match[side + "TeamId"])
        entry = teams[tid] = {"confirmed": lineup(summary, tid)}
        try:
            recent = recent_results(get, tid, min(now, match["scheduledAt"]), eid)
            entry["recent"] = recent
            if recent and not entry["confirmed"]:
                previous = summary_for(get, "all", recent[0]["eventId"])
                entry["previous"] = lineup(previous, tid)
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            log.warning("Historial ESPN incompleto para %s: %s", tid, exc)
    return {"eventId": eid, "observedAt": int(now.timestamp() * 1000), "teams": teams}


def merged(old, fresh):
    old = old or {}
    if old.get("eventId") != fresh["eventId"]:
        old = {}
    if old.get("observedAt", 0) > fresh["observedAt"]:
        return old
    teams = {}
    for tid, side in fresh["teams"].items():
        prior = old.get("teams", {}).get(tid, {})
        recent = side.get("recent", prior.get("recent", []))
        same = [r["eventId"] for r in recent[:1]] == [r["eventId"] for r in prior.get("recent", [])[:1]]
        teams[tid] = {"recent": recent, "confirmed": side.get("confirmed") or prior.get("confirmed"),
                      "previous": side.get("previous") or (prior.get("previous") if same else None)}
    return {**fresh, "teams": teams}


def featured(matches):
    candidates = [(key, m) for key, m in matches.items() if m.get("espnEventId") and m.get("scheduledAt")]
    now = datetime.now(timezone.utc)
    upcoming = sorted(((key, m) for key, m in candidates if m.get("status") == "live"
                       or (m.get("status") == "scheduled" and m["scheduledAt"] > now)),
                      key=lambda item: item[1]["scheduledAt"])
    finished = sorted(((key, m) for key, m in candidates if m.get("status") == "finished"),
                      key=lambda item: item[1]["scheduledAt"], reverse=True)
    return upcoming[:3] + finished[:1]
