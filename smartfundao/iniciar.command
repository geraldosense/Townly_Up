#!/bin/bash
# Townly Up — duplo clique para instalar (1.ª vez) e arrancar o servidor.
# Tudo o que aparece nesta janela fica também guardado em iniciar.log (para diagnosticar erros).
cd "$(dirname "$0")" || exit 1
exec > >(tee iniciar.log) 2>&1

pausa() { echo ""; read -r -p "Carregue Enter para fechar..."; exit 1; }

echo "== Townly Up =="
echo "Pasta: $(pwd)"
echo "Data:  $(date)"
echo "macOS: $(sw_vers -productVersion 2>/dev/null) · $(uname -m)"

# 1) Python 3.9 ou superior (o de python.org ou do Homebrew; o de sistema também serve se for 3.9+)
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  echo ""
  echo "ERRO: não encontrei o Python 3.9 ou mais recente."
  echo "Instale-o em https://www.python.org/downloads/macos/ e volte a fazer duplo clique neste ficheiro."
  pausa
fi
echo "Python: $PY ($("$PY" --version 2>&1))"

# 2) Ambiente virtual (recriado se estiver estragado)
if [ ! -x .venv/bin/python ]; then
  rm -rf .venv
  echo "-> A criar o ambiente virtual (.venv)..."
  "$PY" -m venv .venv || { echo "ERRO ao criar o .venv"; pausa; }
fi
source .venv/bin/activate || { echo "ERRO ao ativar o .venv"; pausa; }

# 3) Dependências
echo "-> A instalar dependências (Flask, Pillow, psycopg, pytest)..."
python -m pip install --disable-pip-version-check -q --upgrade pip
if ! python -m pip install --disable-pip-version-check -q -r requirements.txt; then
  echo ""
  echo "ERRO na instalação das dependências."
  echo "Se aparecer 'CERTIFICATE_VERIFY_FAILED': abra Aplicações > Python 3.x > Install Certificates.command"
  echo "e volte a tentar. Confirme também que tem ligação à internet."
  pausa
fi
python -c "import flask, PIL, psycopg; print('Flask', flask.__version__, '· Pillow', PIL.__version__, '· psycopg', psycopg.__version__)" || pausa

# 4) PostgreSQL (Postgres.app ou Homebrew). Sem PostgreSQL, usa SQLite (instance/townlyup.db).
for d in /Applications/Postgres.app/Contents/Versions/latest/bin /opt/homebrew/bin /usr/local/bin; do
  [ -x "$d/psql" ] && export PATH="$d:$PATH" && break
done
if command -v psql >/dev/null 2>&1 && pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
  if ! psql -h localhost -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='townlyup'" 2>/dev/null | grep -q 1; then
    createdb -h localhost townlyup && echo "   Base de dados PostgreSQL 'townlyup' criada."
  fi
  export DATABASE_URL="postgresql://$USER@localhost:5432/townlyup"
  echo "Base de dados: PostgreSQL ($DATABASE_URL)"
else
  unset DATABASE_URL
  echo "Base de dados: SQLite (instance/townlyup.db)"
  echo "   Para usar PostgreSQL: instale a Postgres.app (https://postgresapp.com), abra-a,"
  echo "   carregue em 'Initialize' e volte a fazer duplo clique neste ficheiro."
fi

# Base SQLite de uma versão anterior (sem migrações)? Guarda-a de lado e começa de novo.
if [ -z "$DATABASE_URL" ] && [ -f instance/townlyup.db ] && \
   ! python -c "import sqlite3,sys; c=sqlite3.connect('instance/townlyup.db'); sys.exit(0 if c.execute(\"SELECT 1 FROM sqlite_master WHERE name='schema_migrations'\").fetchone() else 1)"; then
  mv instance/townlyup.db "instance/townlyup-antigo-$(date +%Y%m%d%H%M).db"
  echo "   A base SQLite antiga foi guardada como instance/townlyup-antigo-*.db"
fi

# 5) Tabelas e dados de demonstração
echo "-> A preparar a base de dados (dados de demonstração na 1.ª vez)..."
flask --app app seed-demo >/dev/null 2>&1 && echo "   Dados de demonstração criados." || echo "   Base de dados já existente — mantida."

# 6) Limites das freguesias (opcional)
if [ ! -f static/data/freguesias_fundao.geojson ]; then
  echo "-> A descarregar os limites das freguesias (CAOP / DGT)..."
  flask --app app freguesias || echo "   (Sem o mapa das freguesias por agora — o resto funciona.)"
fi

# 7) Porta livre (8000; se estiver ocupada tenta 8001, 8002...)
PORT=8000
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  echo "   A porta $PORT está ocupada; a tentar $((PORT + 1))."
  PORT=$((PORT + 1))
done

echo ""
echo "=============================================================="
echo " Servidor em:  http://127.0.0.1:$PORT"
echo " Contas: ana@demo.pt (munícipe) · rui@demo.pt (Câmara)"
echo "         obras@demo.pt (serviço) · junta.alpedrinha@demo.pt (Junta)"
echo "         palavra-passe: Fundao2026"
echo " Deixe esta janela aberta. Para parar: Ctrl + C"
echo "=============================================================="
echo ""
(sleep 3; open "http://127.0.0.1:$PORT") &
flask --app app run --debug --port "$PORT" || pausa
