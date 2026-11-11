#!/bin/bash
# Townly Up — esqueceu a palavra-passe da conta principal? Duplo clique aqui.
# Pede o email (por omissão admin@townly.pt), o nome e uma nova palavra-passe (não aparece enquanto escreve).
cd "$(dirname "$0")" || exit 1
fim() { echo ""; read -r -p "Carregue Enter para fechar..."; exit "${1:-0}"; }
if [ ! -x .venv/bin/python ]; then
  echo "Primeiro faça duplo clique em iniciar.command (instala tudo)."; fim 1
fi
source .venv/bin/activate
if [ -f .env ]; then set -a; source ./.env; set +a; fi
for d in /Applications/Postgres.app/Contents/Versions/latest/bin /opt/homebrew/bin /usr/local/bin; do
  [ -x "$d/psql" ] && export PATH="$d:$PATH" && break
done
if command -v psql >/dev/null 2>&1 && pg_isready -h localhost -p 5432 >/dev/null 2>&1 && \
   psql -h localhost -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='townlyup'" 2>/dev/null | grep -q 1; then
  export DATABASE_URL="postgresql://$USER@localhost:5432/townlyup"
else
  unset DATABASE_URL
fi
echo "== Townly Up · repor a conta principal =="
echo "Enquanto escreve a palavra-passe, nada aparece no ecrã (é normal)."
echo "Regra: 8+ caracteres, com letras e números."
echo ""
flask --app app repor-admin && fim 0 || fim 1
