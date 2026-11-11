"""Geografia no servidor: em que freguesia fica um ponto (limites CAOP / DGT).

Usa static/data/freguesias_fundao.geojson. Se o ficheiro ainda não existir,
parish_at() devolve None e a ocorrência fica entregue à Câmara Municipal.
"""
import json
import os
import threading

_lock = threading.Lock()
_cache = {"mtime": None, "features": []}


def _path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "data", "freguesias_fundao.geojson")


def _polygons(geometry):
    if not geometry:
        return []
    return geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]


def _features():
    path = _path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    with _lock:
        if _cache["mtime"] != mtime:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            feats = []
            for f in data.get("features", []):
                polys = _polygons(f.get("geometry"))
                xs = [p[0] for poly in polys for p in poly[0]]
                ys = [p[1] for poly in polys for p in poly[0]]
                feats.append({"code": str(f["properties"]["dicofre"]), "polys": polys,
                              "bbox": (min(xs), min(ys), max(xs), max(ys))})
            _cache.update(mtime=mtime, features=feats)
        return _cache["features"]


def _in_ring(ring, x, y):
    """Algoritmo de «ray casting»: conta quantas vezes uma semirreta corta o contorno."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def parish_at(lat, lng):
    """Código DICOFRE da freguesia que contém o ponto, ou None."""
    for f in _features():
        x0, y0, x1, y1 = f["bbox"]
        if not (x0 <= lng <= x1 and y0 <= lat <= y1):
            continue
        for poly in f["polys"]:
            if _in_ring(poly[0], lng, lat) and not any(_in_ring(h, lng, lat) for h in poly[1:]):
                return f["code"]
    return None


def interior_point(code):
    """Um ponto garantidamente dentro da freguesia (para os dados de demonstração)."""
    for f in _features():
        if f["code"] != code:
            continue
        x0, y0, x1, y1 = f["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        steps = 24
        candidates = sorted(
            ((x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * j / steps) for i in range(1, steps) for j in range(1, steps)),
            key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2,
        )
        for x, y in candidates:
            if parish_at(y, x) == code:
                return round(y, 6), round(x, 6)
    return None


def available():
    return bool(_features())
