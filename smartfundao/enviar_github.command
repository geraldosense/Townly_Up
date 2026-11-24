#!/bin/bash
# Townly Up — envia a versão atual para o GitHub (github.com/geraldosense/Townly_Up).
# Envia todos os ficheiros da app; o que está no .gitignore (.env com as chaves, base de dados, fotografias) nunca vai.
cd "$(dirname "$0")" || exit 1
echo "== Townly Up: enviar para o GitHub =="
ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" add -A smartfundao
if git -C "$ROOT" diff --cached --quiet; then
  echo "(nada de novo para enviar)"
else
  git -C "$ROOT" commit -m "Townly Up: atualização de $(date '+%d/%m/%Y %H:%M')

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0159C4EZM2L8GGhkKoNKaCCg"
fi
git -C "$ROOT" push origin main
echo
git -C "$ROOT" status -sb | head -3
echo
echo "Concluído. Pode fechar esta janela."
read -n 1 -s -r -p "Carregue numa tecla para fechar..."
