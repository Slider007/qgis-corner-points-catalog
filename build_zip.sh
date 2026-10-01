#!/bin/sh
# Собирает dist/corner_points_catalog-<версия>.zip для «Модули → Установить из ZIP».
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^version=//p' corner_points_catalog/metadata.txt)
mkdir -p dist
ZIP="dist/corner_points_catalog-$VERSION.zip"
rm -f "$ZIP"
zip -qr "$ZIP" corner_points_catalog -x '*__pycache__*' '*.pyc' '*.DS_Store'
echo "$ZIP"
