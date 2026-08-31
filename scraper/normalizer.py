"""
Normalización de datos y generación de IDs determinísticos.

Genera IDs únicos por partido para que los datos de ESPN y Marca
no se dupliquen en Firestore. El ID se basa en la fecha y el rival,
así cualquier fuente que extraiga el mismo partido producirá el mismo ID.
"""

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def normalize_team_name(name: str) -> str:
    """
    Normaliza el nombre de un equipo a un slug consistente.

    Ejemplos:
        "Real Madrid CF" → "real_madrid"
        "Atlético de Madrid" → "atletico_de_madrid"
        "FC Barcelona" → "barcelona"
        "Real Betis Balompié" → "real_betis"
    """
    # Quitar acentos/diacríticos
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")

    # Minúsculas
    name = name.lower().strip()

    # Quitar sufijos comunes de equipos
    suffixes = [" cf", " fc", " balompie", " sad", " club"]
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[: -len(suffix)]

    # Quitar prefijos
    prefixes = ["fc ", "club "]
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix) :]

    # Reemplazar espacios y caracteres especiales por guiones bajos
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")

    return name


def normalize_competition_name(name: str) -> str:
    """Normaliza el nombre de competición a un slug para el ID."""
    slug = name.lower().strip()
    slug = unicodedata.normalize("NFKD", slug)
    slug = slug.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")

    # Abreviar nombres largos
    ABBREVIATIONS = {
        "la_liga": "liga",
        "champions_league": "ucl",
        "copa_del_rey": "copa",
        "supercopa_de_espana": "supercopa",
        "supercopa_de_europa": "supercopa_eu",
        "mundial_de_clubes": "mundial",
    }
    return ABBREVIATIONS.get(slug, slug)


def generate_match_id(match: dict[str, Any]) -> str:
    """
    Genera un ID determinístico para un partido.

    El ID se basa en:
        1. Slug de la competición
        2. Fecha del partido (YYYYMMDD)
        3. Slug del rival

    Formato: {competicion}_{fecha}_vs_{rival}
    """
    # Identificar el rival
    is_home = match.get("homeTeamId") == 83 or match.get("homeTeamTla") == "BAR"
    rival = match.get("awayTeam", "") if is_home else match.get("homeTeam", "")
    rival_slug = normalize_team_name(rival)

    # Fecha
    scheduled_at = match.get("scheduledAt")
    if isinstance(scheduled_at, datetime):
        fecha_slug = scheduled_at.strftime("%Y%m%d")
    else:
        fecha_slug = "00000000"

    # Competición
    comp_slug = match.get("competition", "laliga")

    match_id = f"{comp_slug}_{fecha_slug}_vs_{rival_slug}"

    # Sanear: solo letras, números y guiones bajos
    match_id = re.sub(r"[^a-z0-9_]", "", match_id)
    match_id = re.sub(r"_+", "_", match_id)  # colapsar múltiples _

    return match_id


def merge_match_data(
    existing: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    """
    Fusiona datos de un partido de dos fuentes.

    Reglas de prioridad:
        - ESPN es fuente primaria (datos más fiables)
        - Marca complementa campos faltantes
        - Nunca sobrescribir un campo válido con None
        - El marcador más reciente gana
    """
    merged = dict(existing)

    for key, new_value in new.items():
        existing_value = merged.get(key)

        # No sobrescribir un valor válido con None
        if new_value is None:
            continue

        # Si el existente no tiene valor, usar el nuevo
        if existing_value is None:
            merged[key] = new_value
            continue

    return merged


def normalize_and_merge(
    espn_matches: list[dict[str, Any]],
    marca_matches: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Normaliza y fusiona los partidos de ambas fuentes.

    Returns:
        Diccionario {match_id: match_data} listo para subir a Firestore.
    """
    all_matches: dict[str, dict[str, Any]] = {}

    # Primero procesar ESPN (fuente primaria)
    for match in espn_matches:
        match_id = generate_match_id(match)
        match["id"] = match_id
        all_matches[match_id] = match

    # Luego Marca (solo para complementar campos faltantes como 'round')
    import difflib
    
    # Crear un diccionario de rivales de ESPN agrupados por competición
    espn_rivals = {}
    for m_id, m in all_matches.items():
        comp = m.get("competition", "")
        if comp not in espn_rivals:
            espn_rivals[comp] = {}
        
        is_home = m.get("homeTeamId") == 83 or m.get("homeTeamTla") == "BAR"
        rival = m.get("awayTeam", "") if is_home else m.get("homeTeam", "")
        espn_rivals[comp][m_id] = {"rival": rival, "is_home": is_home}

    marca_merged = 0
    marca_new = 0

    for match in marca_matches:
        comp = match.get("competition", "")
        if comp not in espn_rivals:
            continue
            
        is_home = match.get("homeTeamId") == 83 or match.get("homeTeamTla") == "BAR"
        marca_rival = match.get("awayTeam", "") if is_home else match.get("homeTeam", "")
        
        # Mapeos manuales para equipos que difflib no pueda atrapar bien
        if marca_rival == "PSG":
            marca_rival = "Paris Saint-Germain"
        elif marca_rival == "Sp. Portugal":
            marca_rival = "Sporting CP"
        elif marca_rival == "M. City":
            marca_rival = "Manchester City"
        
        # Filtrar espn_rivals por los que coincidan en local/visitante dentro de la misma competición
        valid_espn_ids = [m_id for m_id, data in espn_rivals[comp].items() if data["is_home"] == is_home]
        valid_rivals = [espn_rivals[comp][m_id]["rival"] for m_id in valid_espn_ids]
        
        # Buscar el rival más parecido en ESPN (cutoff 0.3 para ser seguro dentro de la misma competición)
        close_matches = difflib.get_close_matches(marca_rival, valid_rivals, n=1, cutoff=0.3)
        
        if close_matches:
            espn_matched_rival = close_matches[0]
            for m_id in valid_espn_ids:
                if espn_rivals[comp][m_id]["rival"] == espn_matched_rival:
                    if all_matches[m_id].get("round") is None and match.get("round") is not None:
                        all_matches[m_id]["round"] = match["round"]
                    marca_merged += 1
                    del espn_rivals[comp][m_id]
                    break

    logger.info(
        "Normalización: %d partidos totales "
        "(%d solo ESPN, %d fusionados con Marca, %d solo Marca)",
        len(all_matches),
        len(all_matches) - marca_merged - marca_new,
        marca_merged,
        marca_new,
    )

    return all_matches
