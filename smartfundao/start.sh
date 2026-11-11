#!/usr/bin/env bash
# Arranque em produção (Render ou outro alojamento).
set -e
flask --app app init-db
if [ "${SEED_DEMO:-0}" = "1" ]; then
  flask --app app seed-demo || echo "Dados de demonstração já existem."
fi
exec gunicorn "app:create_app()" --bind "0.0.0.0:${PORT:-8000}" --workers 2 --timeout 60 --access-logfile -
