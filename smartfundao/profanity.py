"""Controlo de linguagem: deteta palavrões e insultos em português (PT e BR) no texto que vai ser publicado.

Funciona sem internet e sem chaves. Antes de comparar, o texto é normalizado para apanhar as fintas habituais:
  • acentos e maiúsculas («MÉRDA» → «merda»);
  • números e símbolos no lugar de letras («m3rd@», «p0rr4», «$hit»);
  • letras repetidas («merdaaaa»);
  • letras separadas por espaços ou pontos («m e r d a», «f.o.d.a»).
Compara palavras inteiras (não partes de palavras), para não recusar nomes como «Condeixa» ou «Cabrela».
"""
import re
import unicodedata

# Palavras exatas (já normalizadas: sem acentos, minúsculas, sem letras repetidas).
_WORDS = {
    # vulgaridades
    "merda", "merdas", "merdoso", "merdosa", "bosta", "bostas", "porra", "porras", "foda", "fodas", "fodase",
    "fodasse", "foder", "fodido", "fodida", "fodidos", "fodidas", "fode", "fodem", "fodes", "fodi", "fodeu",
    "caralho", "caralhos", "carago", "caraças", "cona", "conas", "piça", "pica", "picas", "pila", "pilas",
    "colhoes", "colhao", "tomates", "punheta", "punhetas", "punheteiro", "broche", "broches", "mamada",
    "cu", "cus", "cuzao", "rabeta", "rabetas", "pentelho", "pentelhos", "xota", "xoxota", "buceta", "boceta",
    "pau no cu", "shit", "fuck", "fucking", "fucker", "bitch", "dick", "asshole",
    # insultos
    "puta", "putas", "putedo", "puto de merda", "filhodaputa", "filhadaputa", "fdp", "pqp", "vsf", "crl", "krl",
    "cabrao", "cabroes", "cabra", "corno", "cornos", "cornudo", "paneleiro", "paneleiros", "paneleirice",
    "bicha", "viado", "veado", "viadagem", "maricas", "maricon", "sapatao", "otario", "otaria", "otarios",
    "babaca", "babacas", "imbecil", "imbecis", "idiota", "idiotas", "estupido", "estupida", "estupidos",
    "anormal", "anormais", "atrasado mental", "mongoloide", "retardado", "retardada", "cretino", "cretina",
    "palhaco", "palhacos", "badalhoca", "badalhoco", "vaca", "galdeira", "vadia", "vagabunda", "rameira",
    "prostituta", "chulo", "escumalha", "corja", "merdinhas", "besta", "bestas", "burro de merda",
    # ódio
    "macaco", "macacos", "preto de merda", "pretos de merda", "cigano de merda", "nazi", "heil",
}
# Raízes: apanham qualquer palavra que comece assim (são inconfundíveis).
_STEMS = ("caralh", "fodid", "fodas", "foder", "merdos", "punhet", "paneleir", "filhodaput", "filhadaput",
          "cabroe", "cabrona", "putaria", "puteir", "putedo", "escrot", "arrombad", "fuck", "motherf")
# Insultos «leves» que podem ser usados para descrever coisas (não pessoas): só contam junto a «és/são/seu/sua...»
_SOFT = {"idiota", "idiotas", "estupido", "estupida", "estupidos", "imbecil", "imbecis", "anormal", "anormais",
         "palhaco", "palhacos", "besta", "bestas", "burro", "vaca", "macaco", "macacos", "cretino", "cretina",
         "otario", "otaria", "otarios", "cabra", "corno", "bicha", "tomates", "pica", "pila", "cu", "cus", "fode",
         "fodi", "fodes", "fodem"}
_TARGET = {"es", "sao", "sois", "seu", "sua", "seus", "suas", "voces", "vcs", "tu", "um", "uma", "uns", "umas",
           "teu", "tua", "vossa", "vosso", "cambada", "bando", "grande", "grandessissimo", "no", "pro", "pra"}
_SAFE_CONTEXT = {"tomates": {"de", "cultivo", "plantacao", "horta", "estufa", "caixa", "caixas", "kg"},
                 "pica": {"pau", "agulha"}, "vaca": {"leite", "pasto", "gado", "vacas", "bezerro", "estrada"},
                 "macaco": {"carro", "pneu", "hidraulico"}, "cabra": {"cabras", "rebanho", "pastor", "gado"},
                 "besta": {"carga"}, "bicha": {"fila", "loja", "senha", "farmacia", "multibanco"},
                 "cu": set()}

_LEET = str.maketrans({"0": "o", "1": "i", "!": "i", "3": "e", "4": "a", "@": "a", "5": "s", "$": "s",
                       "7": "t", "8": "b", "9": "g", "€": "e", "|": "i", "+": "t", "*": "", "#": ""})
_PHRASES = {w for w in _WORDS if " " in w}
_SINGLE = _WORDS - _PHRASES


def _fold(text):
    text = unicodedata.normalize("NFD", str(text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("ç", "c")


def _tokens(text):
    t = _fold(text).translate(_LEET)
    t = re.sub(r"(.)\1{1,}", r"\1", t)            # «merdaaa» → «merda» (as listas também não têm letras repetidas)
    raw = re.findall(r"[a-z]+", t)
    # junta letras soltas seguidas: «m e r d a» / «f.o.d.a» → «merda» / «foda»
    out, run = [], []
    for w in raw:
        if len(w) == 1:
            run.append(w)
            continue
        if len(run) >= 3:
            out.append("".join(run))
        else:
            out.extend(run)
        run = []
        out.append(w)
    out.extend(["".join(run)] if len(run) >= 3 else run)
    return out


def _collapse(word):
    return re.sub(r"(.)\1{1,}", r"\1", _fold(word).replace("ç", "c"))


_SINGLE_N = {_collapse(w) for w in _SINGLE}
_SOFT_N = {_collapse(w) for w in _SOFT}
_PHRASES_N = {" ".join(_collapse(p) for p in w.split()) for w in _PHRASES}


def find(text):
    """Devolve a 1.ª expressão ofensiva encontrada (normalizada) ou None."""
    toks = _tokens(text)
    if not toks:
        return None
    joined = " " + " ".join(toks) + " "
    for p in _PHRASES_N:
        if f" {p} " in joined:
            return p
    for i, w in enumerate(toks):
        if w in _SINGLE_N or w.startswith(_STEMS):
            if w in _SOFT_N:
                around = set(toks[max(0, i - 2):i]) | set(toks[i + 1:i + 3])
                if around & _SAFE_CONTEXT.get(w, set()):
                    continue
                if not (set(toks[max(0, i - 2):i]) & _TARGET):
                    continue
            return w
    return None


def check(fields):
    """fields = {nome_do_campo: texto}. Devolve (campo, palavra) da 1.ª ofensa, ou None."""
    for name, text in fields.items():
        hit = find(text)
        if hit:
            return name, hit
    return None
