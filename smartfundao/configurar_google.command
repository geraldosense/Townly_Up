#!/bin/bash
# Townly Up — ligar o «Continuar com o Google».
# Pede o ID de cliente e o segredo (criados na Google Cloud Console) e guarda-os no ficheiro .env desta pasta.
# O segredo não aparece no ecrã e o .env nunca vai para o GitHub.
cd "$(dirname "$0")" || exit 1
clear
echo "=============================================================="
echo " Townly Up · Continuar com o Google"
echo "=============================================================="
echo
echo " Na Google Cloud Console (Credenciais › ID de cliente OAuth › Aplicação Web),"
echo " o «URI de redirecionamento autorizado» tem de ser exatamente:"
echo
echo "     http://localhost:8000/entrar/google/retorno"
echo
read -r -p " Cole o ID de cliente (termina em .apps.googleusercontent.com): " CID
CID="$(echo "$CID" | tr -d '[:space:]')"
case "$CID" in
  *.apps.googleusercontent.com) ;;
  *) echo; echo " Esse não parece um ID de cliente da Google. Nada foi alterado."; read -n 1 -s -r -p " Carregue numa tecla para fechar..."; exit 1;;
esac
read -r -s -p " Cole o segredo do cliente (não aparece ao escrever): " SECRET; echo
SECRET="$(echo "$SECRET" | tr -d '[:space:]')"
if [ ${#SECRET} -lt 10 ]; then echo; echo " Segredo vazio ou curto demais. Nada foi alterado."; read -n 1 -s -r -p " Carregue numa tecla para fechar..."; exit 1; fi

touch .env && chmod 600 .env
# substitui as linhas antigas (se existirem) e junta as novas
grep -v -E '^(GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET)=' .env > .env.tmp 2>/dev/null || true
printf 'GOOGLE_CLIENT_ID=%s\nGOOGLE_CLIENT_SECRET=%s\n' "$CID" "$SECRET" >> .env.tmp
mv .env.tmp .env && chmod 600 .env
grep -q '^\.env$' .gitignore 2>/dev/null || echo ".env" >> .gitignore
echo
echo " ✓ Guardado no ficheiro .env (o segredo não foi mostrado)."
echo
echo " Agora feche a janela do servidor (iniciar.command) e abra-o outra vez."
echo " Depois, em http://localhost:8000/login, carregue em «Continuar com o Google»."
echo
read -n 1 -s -r -p " Carregue numa tecla para fechar..."
