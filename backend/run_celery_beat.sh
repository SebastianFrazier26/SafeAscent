#!/bin/bash
# Start Celery beat scheduler for periodic tasks (cache warming)
#
# Usage: ./run_celery_beat.sh

cd "$(dirname "$0")"

echo "⏰ Starting Celery beat scheduler..."
celery -A app.celery_app beat --loglevel=info
