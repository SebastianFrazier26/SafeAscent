#!/bin/bash
# Start Celery worker for processing background tasks
#
# Usage: ./run_celery_worker.sh

cd "$(dirname "$0")"

echo "🔄 Starting Celery worker..."
celery -A app.celery_app worker --loglevel=info
