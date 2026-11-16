#!/bin/bash
# Townly Up — descarrega o contorno oficial de Portugal (Natural Earth, domínio público) para o mapa sem sessão.
cd "$(dirname "$0")"
curl -sSL -o paises.geojson "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson" && ls -la paises.geojson
echo "FEITO — pode fechar esta janela."
