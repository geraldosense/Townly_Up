#!/usr/bin/env bash
# Arranque em produção (Render ou outro alojamento).
# Todo o conteúdo vem dos utilizadores: a aplicação não tem dados fictícios.
set -e
flask --app app init-db
flask --app app ensure-admin          # 1.ª conta da Câmara (ADMIN_EMAIL / ADMIN_PASSWORD)
exec gunicorn "app:create_app()" --bind "0.0.0.0:${PORT:-8000}" --workers 2 --timeout 60 --access-logfile -
