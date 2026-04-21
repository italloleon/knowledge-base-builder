#!/usr/bin/env sh
set -e

echo "Running Alembic migrations..."
alembic upgrade head
echo "Migrations complete."
exit 0
