#!/bin/bash
# Envia para o GitHub as alterações do README e das imagens do protótipo.
cd "$(dirname "$0")" || exit 1
echo "== Townly Up: enviar para o GitHub =="
git add README.md docs/imagens enviar_github.command
git commit -m "README profissional com imagens do protótipo mobile

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0159C4EZM2L8GGhkKoNKaCCg" || echo "(nada de novo para registar)"
git push origin main
echo
git status -sb | head -3
echo
echo "Concluído. Pode fechar esta janela."
read -n 1 -s -r -p "Carregue numa tecla para fechar..."
