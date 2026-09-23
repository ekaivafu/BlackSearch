#!/bin/bash
# start.sh - Entrypoint for Render / Production
set -e

echo "Running Database Migrations..."
alembic upgrade head

echo "Starting Telegram Bot..."
python -m bot.main
