"""python -m scraper.barca_photos ../fl_predicciones"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from .portraits import ESPN, get_json, resolve, squad, webp


def sync(project):
    directory = project / "assets/player_photos"
    directory.mkdir(parents=True, exist_ok=True)
    catalog = directory / "catalog.json"
    old = json.loads(catalog.read_text()) if catalog.exists() else {}
    players = get_json(ESPN + "/esp.1/teams/83/roster")["athletes"]
    if not players:
        raise ValueError("Plantilla de ESPN vacía")
    members = squad(8634)

    def load(athlete):
        key = str(athlete["id"])
        path = directory / (key + ".webp")
        row = dict(old.get(key, {}), name=athlete["displayName"])
        try:
            fid = row.get("fotmobId") or resolve(athlete, members)
            row["fotmobId"] = fid
            if key == "250465" and fid != "1083323":
                raise ValueError("Revisar identidad de Pedri")
            if fid and not path.exists():
                data = webp(fid)
                temp = path.with_suffix(".tmp")
                temp.write_bytes(data)
                temp.replace(path)
        except Exception as exc:
            print(key, row["name"], exc)
        row["hasPhoto"] = path.is_file() and bool(row.get("fotmobId"))
        return key, row

    with ThreadPoolExecutor(max_workers=4) as pool:
        current = dict(pool.map(load, players))
    # Mantener los assets históricos de quienes ya han salido del equipo.
    result = {**old, **current}
    catalog.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(sum(r["hasPhoto"] for r in current.values()), "/", len(current))
    print("Faltan:", ", ".join(r["name"] for r in current.values() if not r["hasPhoto"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    sync(parser.parse_args().project)
