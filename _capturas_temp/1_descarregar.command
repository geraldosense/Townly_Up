#!/bin/bash
# Townly Up — descarrega as bibliotecas e letras usadas pela app (para as imagens da página «Como funciona»).
cd "$(dirname "$0")"; mkdir -p cdn
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
get() { out="cdn/$(echo "$1" | sed -E 's#^https://##; s#[?&=:;,@]#_#g')"; mkdir -p "$(dirname "$out")"; curl -sSL -A "$UA" -o "$out" "$1" && echo "ok $1"; }
for u in \
 https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css \
 https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2 \
 https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff \
 https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css \
 https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js \
 https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css \
 https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js \
 https://unpkg.com/leaflet@1.9.4/dist/leaflet.css \
 https://unpkg.com/leaflet@1.9.4/dist/leaflet.js \
 https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png \
 https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png \
 https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png ; do get "$u"; done
F='https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap'
curl -sSL -A "$UA" -o cdn/fonts.css "$F" && echo "ok fonts.css"
for w in $(grep -oE 'https://fonts.gstatic.com/[^)]+' cdn/fonts.css | sort -u); do get "$w"; done

# Mapa (Esri World Street Map) da zona do Fundão
mkdir -p tiles
cat tiles.txt | xargs -P 8 -I{} sh -c 'f="tiles/$(echo {} | tr / _).jpg"; [ -s "$f" ] || curl -sS -o "$f" "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{}"'
echo "mapa: $(ls tiles | wc -l) imagens"
rm -f pack.zip; zip -qr pack.zip cdn tiles && echo "pacote: pack.zip"
echo "FEITO — pode fechar esta janela."
