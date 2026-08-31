"""
Fuente de datos: Marca.com (fuente secundaria).

Usa cloudscraper + BeautifulSoup para extraer partidos del calendario
del FC Barcelona desde Marca.com, saltándose protecciones de Cloudflare.

Esta fuente es secundaria: se usa para complementar/validar datos de ESPN.
Si cloudscraper falla (Cloudflare moderno), se degrada a requests y se
logea una advertencia. Los datos de ESPN siguen siendo la fuente primaria.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

import cloudscraper
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MARCA_URLS = [
    "https://www.marca.com/futbol/primera-division/calendario.html",
    "https://www.marca.com/futbol/champions-league/calendario.html"
]
REQUEST_TIMEOUT = 30

# Mapeo de meses en español → número
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "ene": 1, "feb": 2, "mar": 3, "abr": 4,
    "may": 5, "jun": 6, "jul": 7, "ago": 8,
    "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

def fetch_schedule() -> list[dict[str, Any]]:
    """
    Obtiene los partidos del Barça desde Marca.com iterando por las URLs.
    """
    all_matches = []
    
    for url in MARCA_URLS:
        logger.info("Obteniendo calendario de Marca: %s", url)
        html = _get_page_html(url)
        if html:
            comp_slug = "champions_league" if "champions" in url else "laliga"
            matches = _parse_html(html, comp_slug)
            all_matches.extend(matches)
            
    logger.info("Marca: %d partidos obtenidos en total", len(all_matches))
    return all_matches


def _get_page_html(url: str) -> str | None:
    """Obtiene el HTML de la página del calendario de Marca."""
    headers = {
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    # Intento 1: cloudscraper (supera protecciones Cloudflare antiguas)
    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        response = scraper.get(
            url, headers=headers, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        logger.info("Marca: página obtenida con cloudscraper (status %d)", response.status_code)
        return response.text
    except Exception:
        logger.warning(
            "Marca: cloudscraper falló, intentando con requests directamente"
        )

    # Intento 2: requests directo (funciona si no hay Cloudflare activo)
    try:
        response = requests.get(
            MARCA_CALENDAR_URL, headers=headers, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        logger.info("Marca: página obtenida con requests (status %d)", response.status_code)
        return response.text
    except Exception:
        logger.exception("Marca: requests también falló")

    return None


def _parse_html(html: str, comp_slug: str = "laliga") -> list[dict[str, Any]]:
    """Parsea el HTML usando BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    # 1. Buscar en tablas tradicionales de calendario
    tables = soup.find_all("table")
    for table in tables:
        caption = table.find("caption")
        round_info = None
        if caption:
            import re
            m = re.search(r"jornada\s*(\d+)", caption.text, re.IGNORECASE)
            if m:
                round_info = int(m.group(1))
        
        for row in table.find_all("tr"):
            match = _parse_match_row(row, round_info, comp_slug)
            if match is not None:
                matches.append(match)

    if matches:
        return matches

    # 2. Si no hay tablas, buscar divs genéricos
    score_pattern = re.search(r"(\d+)\s*[-–]\s*(\d+)", html)
    if score_pattern:
        logger.debug("Buscando en divs genéricos...")
        for container in soup.select("div.partido, div.match, li.partido"):
            match = _parse_generic_container(container, score_pattern, comp_slug)
            if match is not None:
                matches.append(match)

    return matches


def _parse_match_row(row, round_info: int | None = None, comp_slug: str = "laliga") -> dict[str, Any] | None:
    """Parsea una fila de partido individual."""
    try:
        text = row.get_text(separator=" ", strip=True)
        if not text or len(text) < 5:
            return None

        # Buscar equipos (el Barça debe estar mencionado)
        barca_keywords = ["barcelona", "barça", "fcb", "fc barcelona"]
        text_lower = text.lower()
        if not any(kw in text_lower for kw in barca_keywords):
            return None

        # Buscar marcador
        score_match = re.search(r"(\d+)\s*[-–]\s*(\d+)", text)

        # Buscar rival: intentar extraer de celdas/spans separados
        teams = row.select("td.equipo, span.equipo, a.equipo, td.team, span.team, td.local, td.visitante")
        rival = None
        if teams:
            for team_el in teams:
                # If it has spans with names (like span.equipo_t178), grab those
                span_name = team_el.find("span", class_=lambda c: c and "equipo_t" in c)
                if span_name:
                    team_text = span_name.get_text(strip=True).lower()
                    team_raw = span_name.get_text(strip=True)
                else:
                    team_text = team_el.get_text(strip=True).lower()
                    team_raw = team_el.get_text(strip=True)
                
                if not any(kw in team_text for kw in barca_keywords):
                    rival = team_raw
                    break

        # Buscar fecha
        fecha = _extract_date(row, text)

        # Determinar si es local o visitante de forma segura usando la estructura
        es_local = True
        local_td = row.find("td", class_="local")
        if local_td:
            es_local = any(kw in local_td.get_text(strip=True).lower() for kw in barca_keywords)
        else:
            es_local = _determine_home_away(row, text)

        # Extraer marcador
        goles_barca = None
        goles_rival = None
        estado = "programado"

        if score_match:
            g1, g2 = int(score_match.group(1)), int(score_match.group(2))
            if es_local:
                goles_barca, goles_rival = g1, g2
            else:
                goles_rival, goles_barca = g1, g2
            estado = "finalizado"

        if rival is None:
            rival = _extract_rival_from_text(text)

        if rival is None:
            return None

        home_team = "FC Barcelona" if es_local else rival.strip()
        away_team = rival.strip() if es_local else "FC Barcelona"
        home_goals = goles_barca if es_local else goles_rival
        away_goals = goles_rival if es_local else goles_barca

        status = "finished" if estado == "finalizado" else "scheduled"
        
        # Fecha a datetime (naive UTC here, but it's enough for merge since ESPN provides better date)
        scheduled_at = None
        if fecha:
            try:
                from zoneinfo import ZoneInfo
                dt = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
                scheduled_at = dt.astimezone(ZoneInfo("Europe/Madrid"))
            except Exception:
                pass

        return {
            "homeTeam": home_team,
            "homeTeamId": 83 if es_local else 0,
            "homeTeamTla": "BAR" if es_local else "",
            "homeGoals": home_goals,
            "awayTeam": away_team,
            "awayTeamId": 83 if not es_local else 0,
            "awayTeamTla": "BAR" if not es_local else "",
            "awayGoals": away_goals,
            "competition": comp_slug,
            "season": "",
            "round": round_info,
            "status": status,
            "scheduledAt": scheduled_at,
            "stadium": "",
            "isBarcaMatch": True,
            "manualOverride": False,
            "espnEventId": None,
            "processedAt": None,
        }
    except Exception:
        logger.debug("Error parseando fila de Marca", exc_info=True)
        return None


def _parse_generic_container(container, score_pattern, comp_slug: str = "laliga") -> dict[str, Any] | None:
    """Parsea un contenedor genérico de partido."""
    try:
        text = container.get_text(separator=" ", strip=True)
        barca_keywords = ["barcelona", "barça", "fcb", "fc barcelona"]
        text_lower = text.lower()

        if not any(kw in text_lower for kw in barca_keywords):
            return None

        rival = _extract_rival_from_text(text)
        if rival is None:
            return None

        score_match = score_pattern.search(text)
        goles_barca = None
        goles_rival = None
        marcador = None
        estado = "programado"
        es_local = _determine_home_away(container, text)

        if score_match:
            g1, g2 = int(score_match.group(1)), int(score_match.group(2))
            if es_local:
                goles_barca, goles_rival = g1, g2
            else:
                goles_rival, goles_barca = g1, g2
            marcador = f"{goles_barca}-{goles_rival}"
            estado = "finalizado"

        fecha = _extract_date(container, text)

        home_team = "FC Barcelona" if es_local else rival.strip()
        away_team = rival.strip() if es_local else "FC Barcelona"
        home_goals = goles_barca if es_local else goles_rival
        away_goals = goles_rival if es_local else goles_barca

        status = "finished" if estado == "finalizado" else "scheduled"
        
        scheduled_at = None
        if fecha:
            try:
                from zoneinfo import ZoneInfo
                dt = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
                scheduled_at = dt.astimezone(ZoneInfo("Europe/Madrid"))
            except Exception:
                pass

        return {
            "homeTeam": home_team,
            "homeTeamId": 83 if es_local else 0,
            "homeTeamTla": "BAR" if es_local else "",
            "homeGoals": home_goals,
            "awayTeam": away_team,
            "awayTeamId": 83 if not es_local else 0,
            "awayTeamTla": "BAR" if not es_local else "",
            "awayGoals": away_goals,
            "competition": comp_slug,
            "season": "",
            "round": None,
            "status": status,
            "scheduledAt": scheduled_at,
            "stadium": "",
            "isBarcaMatch": True,
            "manualOverride": False,
            "espnEventId": None,
            "processedAt": None,
        }
    except Exception:
        logger.debug("Error parseando contenedor genérico de Marca", exc_info=True)
        return None


def _extract_rival_from_text(text: str) -> str | None:
    """Intenta extraer el nombre del rival del texto."""
    barca_variants = [
        "FC Barcelona", "Barcelona", "Barça", "FCB",
        "fc barcelona", "barcelona", "barça", "fcb",
    ]

    # Buscar patrón "Equipo1 vs Equipo2" o "Equipo1 - Equipo2"
    vs_patterns = [
        r"(.+?)\s+(?:vs?\.?|[-–])\s+(.+?)(?:\s+\d|\s*$)",
        r"(.+?)\s+(\d+)\s*[-–]\s*(\d+)\s+(.+)",
    ]

    for pattern in vs_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            for group in groups:
                group_clean = group.strip()
                if group_clean and not group_clean.isdigit():
                    is_barca = any(
                        bv.lower() in group_clean.lower() for bv in barca_variants
                    )
                    if not is_barca:
                        return group_clean

    return None


def _extract_date(element, text: str) -> str | None:
    """Intenta extraer la fecha del elemento o del texto."""
    # Buscar en atributos data-*
    for attr in ["data-date", "data-fecha", "datetime"]:
        val = element.get(attr)
        if val:
            return val

    # Buscar en elementos <time>
    time_el = element.find("time")
    if time_el:
        dt = time_el.get("datetime")
        if dt:
            return dt

    # Buscar patrón de fecha en texto: "DD/MM/YYYY", "DD de mes de YYYY"
    date_patterns = [
        (r"(\d{1,2})/(\d{1,2})/(\d{4})", "dmy_slash"),
        (r"(\d{1,2})-(\d{1,2})-(\d{4})", "dmy_dash"),
        (r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", "dmy_text"),
        (r"(\d{4})-(\d{2})-(\d{2})", "ymd_iso"),
    ]

    for pattern, fmt in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                if fmt == "dmy_slash":
                    d, m, y = match.groups()
                    return f"{y}-{int(m):02d}-{int(d):02d}T00:00:00Z"
                elif fmt == "dmy_dash":
                    d, m, y = match.groups()
                    return f"{y}-{int(m):02d}-{int(d):02d}T00:00:00Z"
                elif fmt == "dmy_text":
                    d, month_name, y = match.groups()
                    m = MESES.get(month_name.lower())
                    if m:
                        return f"{y}-{m:02d}-{int(d):02d}T00:00:00Z"
                elif fmt == "ymd_iso":
                    y, m, d = match.groups()
                    return f"{y}-{m}-{d}T00:00:00Z"
            except (ValueError, TypeError):
                continue

    return None


def _determine_home_away(element, text: str) -> bool:
    """Determina si el Barça juega de local. True = local."""
    text_lower = text.lower()

    # Si "barcelona" aparece antes del marcador/guion, es probable local
    barca_pos = -1
    for kw in ["barcelona", "barça", "fcb"]:
        pos = text_lower.find(kw)
        if pos >= 0:
            barca_pos = pos
            break

    dash_pos = text_lower.find(" - ")
    if dash_pos < 0:
        dash_pos = text_lower.find(" vs ")

    if barca_pos >= 0 and dash_pos >= 0:
        return barca_pos < dash_pos

    return True  # default: asumimos local


def _extract_competition(element, text: str) -> str:
    """Intenta extraer la competición del elemento o texto."""
    text_lower = text.lower()
    
    if "champions" in text_lower:
        return "Champions League"
    elif "copa del rey" in text_lower:
        return "Copa del Rey"
    elif "supercopa" in text_lower:
        return "Supercopa de España"
    elif "liga" in text_lower and "fase de liga" not in text_lower:
        return "La Liga"
        
    return "La Liga"
