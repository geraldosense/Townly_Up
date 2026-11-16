#!/bin/bash
# Townly Up — descarrega os limites dos distritos (Natural Earth, domínio público) para o mapa de Portugal.
cd "$(dirname "$0")"
curl -sSL -o regioes.geojson "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson" && ls -la regioes.geojson
echo "FEITO — pode fechar esta janela."
