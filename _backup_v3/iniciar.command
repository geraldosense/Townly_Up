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
echo "-> A instalar dependências (Flask, Pillow, pytest)..."
python -m pip install --disable-pip-version-check -q --upgrade pip
if ! python -m pip install --disable-pip-version-check -q -r requirements.txt; then
  echo ""
  echo "ERRO na instalação das dependências."
  echo "Se aparecer 'CERTIFICATE_VERIFY_FAILED': abra Aplicações > Python 3.x > Install Certificates.command"
  echo "e volte a tentar. Confirme também que tem ligação à internet."
  pausa
fi
python -c "import flask, PIL; print('Flask', flask.__version__, '· Pillow', PIL.__version__)" || pausa

# 4) Base de dados e dados de demonstração
echo "-> A preparar a base de dados (dados de demonstração na 1.ª vez)..."
flask --app app seed-demo >/dev/null 2>&1 && echo "   Dados de demonstração criados." || echo "   Base de dados já existente — mantida."

# 5) Limites das freguesias (opcional)
if [ ! -f static/data/freguesias_fundao.geojson ]; then
  echo "-> A descarregar os limites das freguesias (CAOP / DGT)..."
  flask --app app freguesias || echo "   (Sem o mapa das freguesias por agora — o resto funciona.)"
fi

# 6) Porta livre (8000; se estiver ocupada tenta 8001, 8002...)
PORT=8000
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  echo "   A porta $PORT está ocupada; a tentar $((PORT + 1))."
  PORT=$((PORT + 1))
done

echo ""
echo "=============================================================="
echo " Servidor em:  http://127.0.0.1:$PORT"
echo " Contas: ana@demo.pt (munícipe) · rui@demo.pt (admin)"
echo "         palavra-passe: Fundao2026"
echo " Deixe esta janela aberta. Para parar: Ctrl + C"
echo "=============================================================="
echo ""
(sleep 3; open "http://127.0.0.1:$PORT") &
flask --app app run --debug --port "$PORT" || pausa
