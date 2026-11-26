"""Identidades verificadas con nombre y fecha de nacimiento de ambos proveedores."""
from io import BytesIO
import re
import unicodedata

import requests
from PIL import Image

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer"
FOTMOB = "https://www.fotmob.com/api/data"


def get_json(url, **params):
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode())


def squad(team_id):
    groups = get_json(FOTMOB + "/teams", id=team_id)["squad"]["squad"]
    return [member for group in groups if group["title"] != "coach" for member in group["members"]]


def athlete_names(athlete):
    return {normalized(athlete[k]) for k in ("displayName", "fullName", "shortName") if athlete.get(k)}


def resolve(athlete, members=()):
    birth = (athlete.get("dateOfBirth") or "")[:10]
    if not birth:
        return None
    names = athlete_names(athlete)
    matches = {str(p["id"]) for p in members if normalized(p["name"]) in names and p.get("dateOfBirth") == birth}
    if not matches:
        groups = get_json(FOTMOB + "/search/suggest", term=athlete["displayName"])
        ids = {p["id"] for g in groups for p in g.get("suggestions", [])
               if p.get("type") == "player" and normalized(p["name"]) in names and not p.get("isCoach")}
        for candidate in sorted(ids)[:6]:
            p = get_json(FOTMOB + "/playerData", id=candidate)
            if (str(p.get("id")) == str(candidate) and normalized(p["name"]) in names
                    and (p.get("birthDate") or {}).get("utcTime", "")[:10] == birth):
                matches.add(str(candidate))
    return matches.pop() if len(matches) == 1 else None


def webp(fotmob_id):
    response = requests.get(f"https://images.fotmob.com/image_resources/playerimages/{fotmob_id}.png", timeout=12)
    response.raise_for_status()
    with Image.open(BytesIO(response.content)) as im:
        if im.format != "PNG" or min(im.size) < 48 or max(im.size) > 4096:
            raise ValueError("Retrato inesperado")
        im = im.convert("RGBA")
        if im.getbbox() is None:
            raise ValueError("Retrato vacío")
        im.thumbnail((192, 192), Image.Resampling.LANCZOS)
        out = BytesIO()
        im.save(out, "WEBP", quality=85, method=6)
        return out.getvalue()
