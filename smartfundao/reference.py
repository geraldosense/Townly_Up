"""Dados de referência: freguesias (CAOP/DGT), entidades recetoras e categorias.

São inseridos de forma idempotente (ON CONFLICT DO NOTHING) sempre que a base de dados arranca.
"""
import json
import os

# 23 freguesias do concelho do Fundão — código DICOFRE e nome oficial (CAOP 2022, DGT).
PARISHES = (
    ("050401", "Alcaide"),
    ("050402", "Alcaria"),
    ("050403", "Alcongosta"),
    ("050406", "Alpedrinha"),
    ("050408", "Barroca"),
    ("050410", "Bogas de Cima"),
    ("050411", "Capinha"),
    ("050412", "Castelejo"),
    ("050413", "Castelo Novo"),
    ("050416", "Fatela"),
    ("050419", "Lavacolhos"),
    ("050420", "Orca"),
    ("050421", "Pêro Viseu"),
    ("050424", "Silvares"),
    ("050425", "Soalheira"),
    ("050426", "Souto da Casa"),
    ("050427", "Telhado"),
    ("050431", "Enxames"),
    ("050432", "Três Povos"),
    ("050433", "União das freguesias de Janeiro de Cima e Bogas de Baixo"),
    ("050434", "União das freguesias de Fundão, Valverde, Donas, Aldeia de Joanes e Aldeia Nova do Cabo"),
    ("050435", "União das freguesias de Póvoa de Atalaia e Atalaia do Campo"),
    ("050436", "União das freguesias de Vale de Prazeres e Mata da Rainha"),
)
SEAT_PARISH = "050434"  # sede do concelho

SERVICES = (
    "Obras Municipais",
    "Espaços Verdes",
    "Iluminação Pública",
    "Limpeza Urbana",
    "Trânsito e Sinalização",
    "Águas e Saneamento",
)

# Categoria -> serviço técnico que a recebe por defeito (None = Junta de Freguesia do local).
CATEGORIES = (
    ("Vias e pavimentos", "Obras Municipais"),
    ("Iluminação pública", "Iluminação Pública"),
    ("Espaços verdes", "Espaços Verdes"),
    ("Limpeza urbana", "Limpeza Urbana"),
    ("Mobiliário urbano", None),
    ("Sinalização e trânsito", "Trânsito e Sinalização"),
    ("Água e saneamento", "Águas e Saneamento"),
    ("Outro", None),
)

CAMARA = "Câmara Municipal do Fundão"
CAPTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "freguesias", "legendas.json")


def short_name(name):
    prefix = "União das freguesias de "
    return name[len(prefix):] if name.startswith(prefix) else name


def junta_name(name):
    if name.startswith("União das freguesias de "):
        return "União das Freguesias de " + short_name(name)
    return "Junta de Freguesia de " + name


def load_captions():
    """Legendas opcionais das imagens: static/img/freguesias/legendas.json → {código: {ponto, credito}}."""
    try:
        with open(CAPTIONS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


def seed(db):
    captions = load_captions()
    with db:
        for code, name in PARISHES:
            cap = captions.get(code, {})
            db.execute(
                """INSERT INTO parishes (code, name, short_name, landmark, image_credit) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (code) DO UPDATE SET landmark = excluded.landmark, image_credit = excluded.image_credit""",
                (code, name, short_name(name), cap.get("ponto"), cap.get("credito")),
            )
        db.execute("INSERT INTO organizations (name, kind) VALUES (?, 'camara') ON CONFLICT (name) DO NOTHING", (CAMARA,))
        for service in SERVICES:
            db.execute(
                "INSERT INTO organizations (name, kind, service) VALUES (?, 'servico', ?) ON CONFLICT (name) DO NOTHING",
                (service, service),
            )
        for code, name in PARISHES:
            db.execute(
                "INSERT INTO organizations (name, kind, parish_code) VALUES (?, 'junta', ?) ON CONFLICT (name) DO NOTHING",
                (junta_name(name), code),
            )
        for name, service in CATEGORIES:
            db.execute(
                "INSERT INTO categories (name, default_service) VALUES (?, ?) ON CONFLICT (name) DO NOTHING",
                (name, service),
            )
