# Celery Background Tasks Setup

## Overview

SafeAscent uses Celery for background task processing, specifically for cache warming of popular routes. This pre-calculates safety scores every 6 hours to improve API response times.

## Architecture

- **Broker**: Redis (same instance as cache)
- **Backend**: Redis (for task results)
- **Worker**: Processes background tasks
- **Beat**: Scheduler for periodic tasks (runs every 6 hours)

## Components

### Cache Warming Task

**Task**: `app.tasks.cache_warming.warm_popular_routes_cache`
**Schedule**: Every 6 hours (at 00:00, 06:00, 12:00, 18:00 UTC)
**Purpose**: Pre-calculates safety scores for:
- Top 200 routes (on mountains with highest accident counts)
- All 7 days in the forecast window
- ~1,400 calculations per run

**Cache Strategy**:
- Results stored in Redis with 6-hour TTL
- Matches weather data refresh rate
- Reduces API calculation time from ~500ms to <10ms

## Running Celery

### Development

You need to run **THREE** processes:

1. **FastAPI Server** (main application):
   ```bash
   cd backend
   uvicorn app.main:app --reload --port 8000
   ```

2. **Celery Worker** (processes tasks):
   ```bash
   cd backend
   ./run_celery_worker.sh
   # OR manually:
   celery -A app.celery_app worker --loglevel=info
   ```

3. **Celery Beat** (schedules periodic tasks):
   ```bash
   cd backend
   ./run_celery_beat.sh
   # OR manually:
   celery -A app.celery_app beat --loglevel=info
   ```

### Production

For production, use a process manager like **Supervisor** or **systemd** to manage all three processes.

Example using systemd:

```ini
# /etc/systemd/system/safeascent-celery-worker.service
[Unit]
Description=SafeAscent Celery Worker
After=network.target redis.target

[Service]
Type=forking
User=www-data
WorkingDirectory=/var/www/safeascent/backend
Environment="PATH=/var/www/safeascent/backend/venv/bin"
ExecStart=/var/www/safeascent/backend/venv/bin/celery -A app.celery_app worker --loglevel=info --detach

[Install]
WantedBy=multi-user.target
```

## Manual Cache Warming

To manually trigger cache warming (useful for testing):

```python
from app.tasks.cache_warming import warm_popular_routes_cache

# Call directly (synchronous - will block)
result = warm_popular_routes_cache()
print(result)

# OR call via Celery (asynchronous - returns immediately)
task = warm_popular_routes_cache.delay()
print(f"Task ID: {task.id}")
# Check status later
print(task.ready())  # True if complete
print(task.result)   # Get result
```

## Monitoring

### Check Celery Worker Status

```bash
celery -A app.celery_app inspect active
celery -A app.celery_app inspect stats
```

### Check Scheduled Tasks

```bash
celery -A app.celery_app inspect scheduled
```

### Check Redis Cache

```bash
redis-cli
> KEYS *safety_score*
> TTL safety_score:123:2026-02-01
> GET safety_score:123:2026-02-01
```

## Configuration

Celery settings are in `app/celery_app.py`:

- **Broker URL**: `settings.REDIS_URL` (from .env)
- **Result Backend**: Same as broker
- **Task Serializer**: JSON
- **Result Expires**: 24 hours
- **Beat Schedule**: Every 6 hours (0, 6, 12, 18:00 UTC)

## Troubleshooting

### Worker not picking up tasks

1. Check Redis is running: `redis-cli ping`
2. Check worker is running: `celery -A app.celery_app inspect active`
3. Check logs for errors

### Beat not scheduling tasks

1. Ensure beat is running (only ONE beat instance should run)
2. Check beat schedule: `celery -A app.celery_app inspect scheduled`
3. Remove celerybeat-schedule.db if corrupted

### Cache not warming

1. Check worker logs for errors
2. Verify database connection
3. Check that routes exist with valid lat/lon
4. Manually trigger: `warm_popular_routes_cache.delay()`

## Performance Notes

- Cache warming takes ~10-15 minutes for 200 routes × 7 days
- Uses batching (10 routes at a time) to avoid overwhelming the system
- Failed calculations are logged but don't stop the task
- API endpoints still calculate on-demand if cache misses occur
