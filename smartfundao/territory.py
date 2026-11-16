"""Território: os 18 distritos + 2 regiões autónomas e os 308 concelhos de Portugal.

A lista vem de static/data/portugal.json (GEO API PT, dados CAOP/DGT). A plataforma está preparada para o
país inteiro, mas cada concelho só fica «ativo» quando a sua Câmara aderir: até lá quem lá vive pode criar
conta e ver o mapa, mas aparece «Ainda não disponível no seu concelho».

Um concelho fica ativo quando a conta principal (plataforma) cria a conta da sua Câmara Municipal (Painel › Contas).
ACTIVE guarda só os concelhos com ficheiros de limites próprios em static/data (o Fundão); os outros vêm da GEO API PT.
"""
import json
import os
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))

# Concelhos com ficheiros próprios (contorno e freguesias em static/data). bounds = (lat_min, lat_max, lng_min, lng_max)
ACTIVE = {
    "Fundão": {
        "district": "Castelo Branco",
        "center": (40.1393, -7.5005),
        "bounds": (39.95, 40.35, -7.85, -7.20),
        "outline": "data/concelho_fundao.geojson",
        "parishes": "data/freguesias_fundao.geojson",
    },
}
DEFAULT = "Fundão"


def _load():
    with open(os.path.join(_HERE, "static", "data", "portugal.json"), encoding="utf-8") as fh:
        return json.load(fh)["distritos"]


DISTRICTS = {d["distrito"]: d["concelhos"] for d in _load()}
DISTRICT_NAMES = list(DISTRICTS)
MUNICIPALITY_DISTRICT = {m: d for d, ms in DISTRICTS.items() for m in ms}


def fold(text):
    """Sem acentos e em minúsculas (para comparar nomes)."""
    return "".join(c for c in unicodedata.normalize("NFD", str(text or "")) if unicodedata.category(c) != "Mn").lower().strip()


def valid(district, municipality):
    return district in DISTRICTS and municipality in DISTRICTS[district]


# Concelhos ativos: os que têm a conta da sua Câmara Municipal criada (e ativa) — decidido na base de dados.
# A aplicação liga aqui uma função que devolve esse conjunto (ver app.py); sem ela (scripts), vale ACTIVE.
_active_source = None


def active_set():
    if _active_source is not None:
        try:
            return set(_active_source())
        except Exception:
            pass
    return set(ACTIVE)


def is_active(municipality):
    return municipality in active_set()


def info(municipality):
    """Dados do concelho para o mapa (o do Fundão se o concelho ainda não aderiu)."""
    key = municipality if municipality in ACTIVE else DEFAULT
    return {"name": key, **ACTIVE[key], "active": municipality in ACTIVE if municipality else True}


def options():
    """Para o formulário (selects encadeados): {distrito: [concelhos...]} + quais estão ativos."""
    return {"districts": DISTRICTS, "active": sorted(active_set())}


# ---------------------------------------------------------------- Limites (contorno do concelho e freguesias), país inteiro
# Fundão: ficheiros locais (static/data). Restantes concelhos: GEO API PT (json.geoapi.pt, dados CAOP da DGT),
# descarregados uma vez e guardados em instance/geo/ — depois funcionam sem internet.
import re as _re
import threading as _threading
import urllib.parse as _urlparse
import urllib.request as _urlrequest

_geo_lock = _threading.Lock()
_geo_mem = {}
GEOAPI = "https://json.geoapi.pt/municipio/{}"


def _ssl_context():
    """Certificados fiáveis (o Python de python.org no Mac não usa os do sistema)."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class GeometryUnavailable(Exception):
    """Não foi possível obter os limites (sem internet e sem cópia guardada)."""


def _round(coords):
    if isinstance(coords, (int, float)):
        return round(coords, 5)
    return [_round(c) for c in coords]


def _bbox(features):
    xs, ys = [], []
    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    for f in features:
        if f.get("geometry"):
            walk(f["geometry"]["coordinates"])
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


SIMPLIFY = 0.00015   # ~15 m: invisível no mapa, mas os ficheiros ficam 5–20× mais pequenos (melhor no telemóvel)


def _dp(points, tol):
    """Douglas–Peucker (iterativo): retira pontos que não mudam a forma mais do que `tol` graus."""
    if len(points) < 5:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a][:2], points[b][:2]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            x, y = points[i][:2]
            d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
            if d > best:
                best, idx = d, i
        if best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    out = [p for p, k in zip(points, keep) if k]
    return out if len(out) >= 4 else points


def _simplify(geometry):
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    def ring_dp(ring):
        if len(ring) > 8 and ring[0][:2] == ring[-1][:2]:      # anel fechado: simplifica as duas metades
            mid = len(ring) // 2
            return _dp(ring[:mid + 1], SIMPLIFY)[:-1] + _dp(ring[mid:], SIMPLIFY)
        return _dp(ring, SIMPLIFY)
    polys = [[_round(ring_dp(ring)) for ring in poly] for poly in polys]
    return {"type": geometry["type"], "coordinates": polys if geometry["type"] == "MultiPolygon" else polys[0]}


def _feature(geometry, **props):
    return {"type": "Feature", "properties": props, "geometry": _simplify(geometry)}


def _from_geoapi(data, name):
    gj = data.get("geojsons") or {}
    mun = gj.get("municipio")
    if not mun or not mun.get("geometry"):
        raise GeometryUnavailable(name)
    parishes = []
    for f in gj.get("freguesias") or []:
        p = f.get("properties") or {}
        code = str(p.get("Dicofre") or p.get("DICOFRE") or p.get("dtmnfr") or "")
        label = p.get("Freguesia") or p.get("nome") or p.get("freguesia") or code
        if f.get("geometry") and code:
            parishes.append(_feature(f["geometry"], dicofre=code, freguesia=label))
    outline = _feature(mun["geometry"], concelho=name)
    return {"v": 2, "name": name, "district": MUNICIPALITY_DISTRICT.get(name), "active": is_active(name),
            "bbox": _bbox([outline]), "outline": outline,
            "parishes": {"type": "FeatureCollection", "features": sorted(parishes, key=lambda f: fold(f["properties"]["freguesia"]))}}


def _local(name):
    """Concelhos com ficheiros próprios em static/data (o Fundão)."""
    cfg = ACTIVE[name]
    with open(os.path.join(_HERE, "static", cfg["outline"]), encoding="utf-8") as fh:
        outline = json.load(fh)["features"][0]
    with open(os.path.join(_HERE, "static", cfg["parishes"]), encoding="utf-8") as fh:
        parishes = json.load(fh)
    feats = [{"type": "Feature", "properties": {"dicofre": str(f["properties"]["dicofre"]), "freguesia": f["properties"]["freguesia"]},
              "geometry": f["geometry"]} for f in parishes["features"]]
    return {"name": name, "district": cfg["district"], "active": True, "bbox": _bbox([outline]), "outline": outline,
            "parishes": {"type": "FeatureCollection", "features": feats}}


def _slug(name):
    return _re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-")


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def geometry(name, cache_dir=None, fetch=True, timeout=8):
    """Contorno e freguesias de um concelho. Lança GeometryUnavailable se não houver forma de os obter."""
    if name not in MUNICIPALITY_DISTRICT:
        raise KeyError(name)
    with _geo_lock:
        if name in _geo_mem:
            return _geo_mem[name]
    if name in ACTIVE:
        data = _local(name)
    else:
        path = os.path.join(cache_dir, "geo", _slug(name) + ".json") if cache_dir else None
        data = None
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("v") != 2:                         # cópia antiga sem simplificação: simplifica e regrava
                data["outline"]["geometry"] = _simplify(data["outline"]["geometry"])
                for f in data["parishes"]["features"]:
                    f["geometry"] = _simplify(f["geometry"])
                data["v"] = 2
                _save(path, data)
        elif fetch:
            req = _urlrequest.Request(GEOAPI.format(_urlparse.quote(name)), headers={"User-Agent": "TownlyUp/1.0 (PAP)", "Accept": "application/json"})
            try:
                with _urlrequest.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                    data = _from_geoapi(json.loads(resp.read().decode("utf-8")), name)
            except GeometryUnavailable:
                raise
            except Exception as exc:
                raise GeometryUnavailable(f"{name}: {exc}") from None
            if path:
                _save(path, data)
        if data is None:
            raise GeometryUnavailable(name)
        data["active"] = is_active(name)
    with _geo_lock:
        _geo_mem[name] = data
    return data


def parish_codes(name, cache_dir=None):
    """Códigos (DICOFRE) e nomes das freguesias de um concelho, ou [] se os limites não estiverem disponíveis."""
    try:
        g = geometry(name, cache_dir)
    except (GeometryUnavailable, KeyError):
        return []
    return [(f["properties"]["dicofre"], f["properties"]["freguesia"]) for f in g["parishes"]["features"]]
