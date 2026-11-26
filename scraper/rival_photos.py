"""Retratos compartidos entre encuentros. La escritura condicional protege ante dos workers."""
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from threading import Lock
from urllib.parse import quote
from uuid import uuid4

import requests

from .portraits import ESPN, get_json, resolve, webp

log = logging.getLogger(__name__)


def photo_url(bucket, player_id, find_identity):
    from google.api_core.exceptions import NotFound, PreconditionFailed

    path = f"player-photos/{player_id}.webp"
    blob = bucket.blob(path)
    try:
        blob.reload()
    except NotFound:
        fid = find_identity(player_id)
        if not fid:
            return None
        content = webp(fid)
        blob.metadata = {"firebaseStorageDownloadTokens": str(uuid4()), "fotmobId": fid}
        blob.cache_control = "public,max-age=31536000,immutable"
        try:
            blob.upload_from_string(content, content_type="image/webp", if_generation_match=0)
        except PreconditionFailed:
            blob.reload()

    metadata = blob.metadata or {}
    token = metadata.get("firebaseStorageDownloadTokens")
    if not token:
        token = str(uuid4())
        blob.metadata = {**metadata, "firebaseStorageDownloadTokens": token}
        try:
            blob.patch(if_metageneration_match=blob.metageneration)
        except PreconditionFailed:
            blob.reload()
            token = (blob.metadata or {}).get("firebaseStorageDownloadTokens")
    if token:
        return f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{quote(path, safe='')}?alt=media&token={token.split(',')[0]}"
    return None


def sync(match, doc):
    from firebase_admin import storage

    snapshot = doc.get().to_dict() or {}
    preview = snapshot.get("preview") or {}
    if preview.get("eventId") != str(match["espnEventId"]):
        return
    known = snapshot.get("playerPhotoUrls", {})
    for tid, side in preview["teams"].items():
        if tid == "83":
            continue
        lineup = side.get("confirmed") or side.get("previous") or {}
        ids = {str(p["id"]) for p in lineup.get("players", []) if str(p.get("id", "")).isdigit()} - known.keys()
        if not ids:
            continue
        bucket = storage.bucket(os.environ.get("PLAYER_PHOTOS_BUCKET") or "fl-predicciones.firebasestorage.app")
        roster = None
        lock = Lock()

        def find_identity(pid):
            nonlocal roster
            with lock:
                if roster is None:
                    try:
                        players = get_json(f"{ESPN}/all/teams/{tid}/roster")["athletes"]
                        roster = {str(p["id"]): p for p in players}
                    except (requests.RequestException, KeyError):
                        roster = {}
            athlete = roster.get(pid)
            if not athlete:
                league = match.get("espnLeague") or "esp.1"
                athlete = get_json(f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{league}/athletes/{pid}")
            return resolve(athlete) if str(athlete.get("id")) == pid else None

        def fetch(pid):
            try:
                return pid, photo_url(bucket, pid, find_identity)
            except Exception as exc:
                log.warning("Foto ESPN %s pendiente (%s)", pid, type(exc).__name__)
                return pid, None

        with ThreadPoolExecutor(max_workers=4) as pool:
            updates = {f"playerPhotoUrls.{pid}": url for pid, url in pool.map(fetch, sorted(ids)) if url}
        if updates:
            doc.update(updates)
