"""
Script principal del bot de scraping del FC Barcelona.

Orquesta el flujo completo:
    1. Obtiene partidos de ESPN (fuente primaria, API JSON)
    2. Obtiene partidos de Marca (fuente secundaria, scraping HTML)
    3. Normaliza y fusiona datos (deduplicación por ID determinístico)
    4. Sube a Cloud Firestore con set(merge=True)

Uso:
    # Ejecución normal (sube a Firestore)
    python -m scraper.main

    # Dry run (solo muestra datos, no escribe en Firestore)
    python -m scraper.main --dry-run

    # Solo ESPN
    python -m scraper.main --source espn

    # Solo Marca
    python -m scraper.main --source marca
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from scraper.sources import espn, marca
from scraper.normalizer import normalize_and_merge
from scraper.firestore_client import upload_matches

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scraper")


def main() -> int:
    """Punto de entrada principal del scraper."""
    args = parse_args()

    logger.info("=" * 60)
    logger.info("🟦🟥 Bot Scraping FC Barcelona — Inicio")
    logger.info("Hora: %s", datetime.now(timezone.utc).isoformat())
    logger.info("Modo: %s", "DRY RUN" if args.dry_run else "PRODUCCIÓN")
    logger.info("Fuente: %s", args.source)
    logger.info("=" * 60)

    # ── Paso 1: Obtener datos de ESPN (fuente primaria) ──
    espn_matches = []
    marca_matches = []

    if args.source in ("all", "espn"):
        logger.info("📡 Obteniendo datos de ESPN...")
        try:
            espn_matches = espn.fetch_schedule()
        except Exception:
            logger.exception("❌ Error crítico obteniendo datos de ESPN")

    # ── Paso 2: Obtener datos de Marca (para complementar la jornada) ──
    if args.source in ("all", "marca"):
        logger.info("📰 Obteniendo datos de Marca para complementar (ej. jornadas)...")
        try:
            marca_matches = marca.fetch_schedule()
        except Exception:
            logger.exception("⚠️  Error obteniendo datos de Marca (secundario)")

    # ── Paso 3: Verificar que tenemos datos ──
    total_raw = len(espn_matches) + len(marca_matches)
    if total_raw == 0:
        logger.error("❌ No se obtuvieron partidos de ninguna fuente. Abortando.")
        return 1

    logger.info(
        "📊 Datos crudos: %d de ESPN + %d de Marca = %d total",
        len(espn_matches),
        len(marca_matches),
        total_raw,
    )

    # ── Paso 3: Normalizar y fusionar ──
    logger.info("🔄 Normalizando y fusionando datos...")
    merged_matches = normalize_and_merge(espn_matches, marca_matches)

    logger.info("✅ %d partidos únicos después de fusión", len(merged_matches))

    # ── Paso 4: Mostrar resumen ──
    _print_summary(merged_matches)

    # ── Paso 5: Subir a Firestore ──
    if args.dry_run:
        logger.info("🔍 [DRY RUN] Mostrando datos que se subirían:")
        print(json.dumps(
            {k: v for k, v in list(merged_matches.items())[:5]},
            indent=2,
            ensure_ascii=False,
            default=str,
        ))
        if len(merged_matches) > 5:
            print(f"... y {len(merged_matches) - 5} partidos más")
    else:
        logger.info("🔥 Subiendo a Cloud Firestore...")

    uploaded = upload_matches(merged_matches, dry_run=args.dry_run)

    logger.info("=" * 60)
    logger.info("✅ Finalizado: %d partidos procesados", uploaded)
    logger.info("=" * 60)

    # Modo Live Tracking para GitHub Actions
    if args.dry_run:
        logger.info("Modo DRY RUN: finalizando tras la primera ejecución.")
        return 0

    import time
    import random

    # Variables para detectar goles
    prev_home_goals = None
    prev_away_goals = None

    # Silenciar logs ruidosos durante el bucle en vivo
    logging.getLogger("scraper.normalizer").setLevel(logging.WARNING)
    logging.getLogger("scraper.firestore_client").setLevel(logging.WARNING)

    while True:
        now = datetime.now(timezone.utc)
        target_match = None
        target_match_id = None
        
        # 1. Buscar si hay alguno en vivo
        for m_id, m in merged_matches.items():
            if m.get("status") == "live":
                target_match = m
                target_match_id = m_id
                break
                
        # 2. Si no hay en vivo, buscar el próximo que empiece en menos de 8 horas
        if not target_match:
            future_matches = []
            for m_id, m in merged_matches.items():
                if m.get("status") == "scheduled" and m.get("scheduledAt"):
                    dt = m["scheduledAt"]
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                        
                    delta_hours = (dt - now).total_seconds() / 3600.0
                    # Permitir delta negativo (-4h) por si ESPN tarda unos minutos en cambiar el estado a live
                    if -4<= delta_hours <= 2.1:
                        future_matches.append((delta_hours, m_id, m))
            
            if future_matches:
                future_matches.sort(key=lambda x: x[0])
                target_match_id = future_matches[0][1]
                target_match = future_matches[0][2]

        if not target_match:
            # Restaurar logs por si acaso antes de salir
            logging.getLogger("scraper.normalizer").setLevel(logging.INFO)
            logger.info("⚽ No hay partidos en vivo ni programados en las próximas 2 horas. Apagando bot hasta el próximo cron.")
            break

        status = target_match.get("status")
        match_time = target_match.get("scheduledAt")
        if match_time.tzinfo is None:
            match_time = match_time.replace(tzinfo=timezone.utc)

        if status == "scheduled":
            sleep_secs = (match_time - now).total_seconds()
            if sleep_secs > 0:
                logger.info("Próximo partido a las %s. Durmiendo %.1f minutos...", match_time.strftime("%H:%M UTC"), sleep_secs / 60)
                time.sleep(sleep_secs)
            else:
                logger.info("El partido debería haber empezado, pero ESPN aún dice 'scheduled'. Esperando 5 minutos...")
                time.sleep(300)
                
        elif status == "live":
            mins_elapsed = (now - match_time).total_seconds() / 60.0
            
            # Consultar siempre cada 1 minuto exactamente
            wait_mins = 1
            
            logger.info("Minuto ~%.0f. Próxima lectura en %d min...", mins_elapsed, wait_mins)
            time.sleep(wait_mins * 60)

        # Volver a descargar datos de forma silenciosa
        espn_matches = []
        marca_matches = []
        
        # Silenciar los logs de ESPN durante el bucle
        logging.getLogger("scraper.sources.espn").setLevel(logging.WARNING)
        
        if args.source in ("all", "espn"):
            espn_matches = espn.fetch_schedule()
        if args.source in ("all", "marca"):
            marca_matches = marca.fetch_schedule()
            
        merged_matches = normalize_and_merge(espn_matches, marca_matches)
        
        # Subir SOLO los campos de estado y goles del partido que estamos siguiendo
        if target_match_id in merged_matches:
            updated_match = merged_matches[target_match_id]
            curr_home = updated_match.get("homeGoals")
            curr_away = updated_match.get("awayGoals")
            
            # Detectar si hay gol
            if prev_home_goals is not None and curr_home is not None and curr_home > prev_home_goals:
                logger.info("¡GOOOOOOL DEL %s!", updated_match.get("homeTeam").upper())
            if prev_away_goals is not None and curr_away is not None and curr_away > prev_away_goals:
                logger.info("¡GOOOOOOL DEL %s!", updated_match.get("awayTeam").upper())
                
            if curr_home is not None and curr_away is not None:
                logger.info("🏟️ MARCADOR: %s %d - %d %s", 
                            updated_match.get("homeTeam"), curr_home, 
                            curr_away, updated_match.get("awayTeam"))
                            
            prev_home_goals = curr_home
            prev_away_goals = curr_away

            minimal_update = {
                target_match_id: {
                    "status": updated_match.get("status"),
                    "homeGoals": curr_home,
                    "awayGoals": curr_away
                }
            }
            upload_matches(minimal_update, dry_run=args.dry_run)

    return 0


def _print_summary(matches: dict) -> None:
    """Imprime un resumen de los partidos por estado."""
    states = {}
    competitions = {}
    for match_data in matches.values():
        state = match_data.get("status", "unknown")
        states[state] = states.get(state, 0) + 1

        comp = match_data.get("competition", "unknown")
        competitions[comp] = competitions.get(comp, 0) + 1

    logger.info("📋 Resumen por estado:")
    for state, count in sorted(states.items()):
        emoji = {
            "scheduled": "📅",
            "live": "🔴",
            "finished": "✅",
            "postponed": "⏸️",
            "canceled": "❌",
            "suspended": "⚠️",
        }.get(state, "❓")
        logger.info("   %s %s: %d", emoji, state, count)

    logger.info("📋 Resumen por competición:")
    for comp, count in sorted(competitions.items()):
        logger.info("   🏆 %s: %d", comp, count)


def parse_args() -> argparse.Namespace:
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Bot de scraping de partidos del FC Barcelona → Firestore",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No escribir en Firestore, solo mostrar datos",
    )
    parser.add_argument(
        "--source",
        choices=["all", "espn", "marca"],
        default="all",
        help="Fuente de datos a usar (default: all)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
