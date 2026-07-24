#!/bin/sh
set -e
DATA_ROOT="${MINI_MARIE_DATA_DIR:-/app/data}"
mkdir -p "$DATA_ROOT/mini_marie_cache/mof_competency"
mkdir -p "$DATA_ROOT/mini_marie_cache/twa_city"
mkdir -p "$DATA_ROOT/mini_marie_cache/chemistry"
mkdir -p "$DATA_ROOT/log"
mkdir -p /app/data /app/raw_data /app/configs
exec "$@"
