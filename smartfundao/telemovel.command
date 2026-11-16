#!/bin/bash
# Townly Up — abrir a app no TELEMÓVEL (mesma rede Wi-Fi que o Mac).
# Arranca um segundo servidor, em HTTPS, na porta 8443 (a mesma base de dados do iniciar.command).
# HTTPS é preciso para o telemóvel deixar usar o GPS («usar a minha localização») e instalar a app.
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

python -m pip install --disable-pip-version-check -q qrcode >/dev/null 2>&1   # só para o código QR (opcional)

# Endereço do Mac na rede Wi-Fi
IP=""
for i in en0 en1 en2; do IP=$(ipconfig getifaddr "$i" 2>/dev/null) && [ -n "$IP" ] && break; done
if [ -z "$IP" ]; then
  echo "ERRO: o Mac não está ligado a uma rede Wi-Fi/cabo. Ligue-o à mesma rede do telemóvel."; fim 1
fi
# Certificado HTTPS feito por este Mac (openssl do macOS; não precisa de instalar nada).
# Guardado em instance/ e refeito se o endereço do Mac na rede mudar.
CERT="instance/telemovel-$IP.pem"; KEY="instance/telemovel-$IP.key"
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  echo "-> A criar o certificado HTTPS para $IP..."
  mkdir -p instance
  CONF=$(mktemp)
  cat > "$CONF" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = Townly Up (teste local)
O = Townly Up
[ext]
subjectAltName = IP:$IP, DNS:localhost
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
CNF
  /usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 365 \
    -keyout "$KEY" -out "$CERT" -config "$CONF" >/dev/null 2>&1 || { rm -f "$CONF"; echo "ERRO ao criar o certificado."; fim 1; }
  rm -f "$CONF"
fi

PORT=8443
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do PORT=$((PORT + 1)); done
URL="https://$IP:$PORT"

echo ""
echo "=============================================================="
echo " No TELEMÓVEL (ligado ao MESMO Wi-Fi que este Mac), abra:"
echo ""
echo "     $URL"
echo ""
echo " ou aponte a câmara do telemóvel para este código:"
python - "$URL" 2>/dev/null <<'PY'
import sys, qrcode
qr = qrcode.QRCode(border=2); qr.add_data(sys.argv[1]); qr.make()
qr.print_ascii(invert=True)
PY
[ $? -ne 0 ] && echo "   (código QR indisponível — escreva o endereço acima)"
echo " 1.ª vez: o telemóvel avisa «ligação não privada» (o certificado é"
echo " feito por este Mac). iPhone: «Mostrar detalhes» → «visitar este site»."
echo " Android/Chrome: «Avançadas» → «Continuar para ${IP}»."
echo " Se o Mac perguntar se o Python pode aceitar ligações: «Permitir»."
echo " Deixe esta janela aberta. Para parar: Ctrl + C"
echo "=============================================================="
echo ""
export TEMPLATES_AUTO_RELOAD=1   # as páginas mudadas aparecem logo, sem reiniciar
flask --app app run --host 0.0.0.0 --port "$PORT" --cert "$CERT" --key "$KEY" --reload --no-debugger || fim 1
