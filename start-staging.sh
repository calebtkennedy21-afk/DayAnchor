#!/usr/bin/env sh
set -eu

python -m staging_database
exec sh ./start.sh
