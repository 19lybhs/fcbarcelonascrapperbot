"""
Cliente de Cloud Firestore para subir datos de partidos.

Usa firebase-admin y el método set(merge=True) para actualizar
documentos sin sobrescribir campos que la app Flutter pueda estar usando
(ej. predicciones de usuarios, likes, comentarios).
"""

import json
import logging
import os
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

# Nombre de la colección en Firestore
COLLECTION_NAME = "matches"

# Máximo de operaciones por batch de Firestore
MAX_BATCH_SIZE = 500

# Variable de entorno con las credenciales como JSON string
ENV_CREDENTIALS_KEY = "FIREBASE_SERVICE_ACCOUNT_JSON"


def _initialize_firebase() -> None:
    """
    Inicializa Firebase Admin SDK.

    Busca credenciales en este orden:
        1. Variable de entorno FIREBASE_SERVICE_ACCOUNT_JSON (para CI/CD)
        2. Archivo serviceAccountKey.json (para desarrollo local)
    """
    if firebase_admin._apps:
        return  # Ya inicializado

    cred = None

    # Opción 1: Variable de entorno (GitHub Actions)
    creds_json = os.environ.get(ENV_CREDENTIALS_KEY)
    if creds_json:
        try:
            creds_dict = json.loads(creds_json)
            cred = credentials.Certificate(creds_dict)
            logger.info("Firebase: credenciales cargadas desde variable de entorno")
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Firebase: error parseando credenciales de env: %s", e)

    # Opción 2: Archivo local
    if cred is None:
        local_paths = [
            "serviceAccountKey.json",
            os.path.join(os.path.dirname(__file__), "..", "serviceAccountKey.json"),
        ]
        for path in local_paths:
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path):
                cred = credentials.Certificate(abs_path)
                logger.info("Firebase: credenciales cargadas desde %s", abs_path)
                break

    if cred is None:
        raise RuntimeError(
            "No se encontraron credenciales de Firebase. "
            f"Configura la variable de entorno '{ENV_CREDENTIALS_KEY}' "
            "o coloca un archivo 'serviceAccountKey.json' en la raíz del proyecto."
        )

    firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin SDK inicializado correctamente")


def get_firestore_client() -> firestore.firestore.Client:
    """Obtiene el cliente de Firestore, inicializando Firebase si es necesario."""
    _initialize_firebase()
    return firestore.client()


def upload_matches(
    matches: dict[str, dict[str, Any]],
    dry_run: bool = False,
) -> int:
    """
    Sube los partidos a Firestore usando set(merge=True).

    Usa batched writes para eficiencia. Cada batch puede contener
    hasta 500 operaciones (límite de Firestore).

    Args:
        matches: Diccionario {match_id: match_data} a subir.
        dry_run: Si es True, no escribe en Firestore (solo logea).

    Returns:
        Número de documentos actualizados/creados.
    """
    if not matches:
        logger.info("No hay partidos para subir a Firestore")
        return 0

    if dry_run:
        logger.info(
            "[DRY RUN] Se subirían %d partidos a Firestore", len(matches)
        )
        for match_id, data in matches.items():
            home = data.get("homeTeamTla", "UNK")
            away = data.get("awayTeamTla", "UNK")
            date_obj = data.get("scheduledAt")
            date_str = date_obj.strftime("%Y-%m-%dT%H:%M") if date_obj else "?"
            logger.info(
                "  [DRY RUN] %s → %s vs %s (%s) - %s",
                match_id,
                home,
                away,
                date_str,
                data.get("status", "?"),
            )
        return len(matches)

    db = get_firestore_client()
    collection_ref = db.collection(COLLECTION_NAME)

    # Leer los documentos existentes para proteger el campo 'round' (jornada).
    # Solo hace falta si de verdad vamos a escribir 'round' en algún partido:
    # las actualizaciones mínimas del bucle de seguimiento en vivo (cada 1
    # minuto, solo status/goles) nunca lo incluyen, así que nos ahorramos
    # leer la colección entera en cada una de esas subidas.
    existing_rounds = {}
    if any("round" in data for data in matches.values()):
        try:
            # stream() cuesta muy pocas lecturas (<50), ideal para proteger datos manuales
            for doc in collection_ref.stream():
                d = doc.to_dict()
                if d and d.get("round") is not None:
                    existing_rounds[doc.id] = d.get("round")
        except Exception as e:
            logger.warning("No se pudieron leer las jornadas existentes: %s", e)

    # Procesar en batches
    match_items = list(matches.items())
    total_uploaded = 0

    for i in range(0, len(match_items), MAX_BATCH_SIZE):
        batch = db.batch()
        batch_items = match_items[i : i + MAX_BATCH_SIZE]

        for match_id, data in batch_items:
            # Proteger la jornada: si ya existe en Firestore, no la sobreescribimos jamás
            if match_id in existing_rounds and "round" in data:
                del data["round"]
                
            doc_ref = collection_ref.document(match_id)
            # merge=True: solo actualiza los campos proporcionados,
            # no sobrescribe campos que existan en el documento
            # (ej. predicciones de usuarios de tu app Flutter)
            batch.set(doc_ref, data, merge=True)

        batch.commit()
        total_uploaded += len(batch_items)
        logger.info(
            "Firestore batch %d: %d documentos escritos (total: %d/%d)",
            (i // MAX_BATCH_SIZE) + 1,
            len(batch_items),
            total_uploaded,
            len(matches),
        )

    logger.info(
        "Firestore: %d documentos actualizados en '%s'",
        total_uploaded,
        COLLECTION_NAME,
    )
    return total_uploaded
