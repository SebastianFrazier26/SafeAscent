# SafeAscent Deployment Guide

**Stack:** Railway (hosting) + Neon (PostgreSQL + PostGIS) + Porkbun (domain)

**Estimated Monthly Cost:** ~$11/month
- Railway: ~$10/mo (backend, frontend, Redis)
- Neon: $0 (free tier)
- Domain: ~$1/mo ($10.87/year)

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Step 1: Purchase Domain (Porkbun)](#step-1-purchase-domain-porkbun)
3. [Step 2: Set Up Database (Neon)](#step-2-set-up-database-neon)
4. [Step 3: Deploy to Railway](#step-3-deploy-to-railway)
5. [Step 4: Connect Custom Domain](#step-4-connect-custom-domain)
6. [Step 5: Migrate Data](#step-5-migrate-data)
7. [Step 6: Verify Deployment](#step-6-verify-deployment)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before starting, you'll need:

| Item | Where to Get It | Notes |
|------|-----------------|-------|
| GitHub account | [github.com](https://github.com) | SafeAscent repo should be pushed |
| OpenWeather API Key | [openweathermap.org/api](https://openweathermap.org/api) | Free tier works |
| Mapbox Token | [mapbox.com](https://account.mapbox.com) | Free tier: 50k loads/month |
| Credit card | - | For Railway (~$5 free credit) and Porkbun |

---

## Step 1: Purchase Domain (Porkbun)

**Time: 5 minutes**

### 1.1 Create Account
1. Go to [porkbun.com](https://porkbun.com)
2. Click **Account** → **Create Account**
3. Fill in details and verify email

### 1.2 Search and Purchase
1. Search for `safeascent.app`
2. Click **Add to Cart** (~$10.87/year)
3. Checkout with credit card
4. **Skip DNS setup for now** - we'll configure it after Railway

### 1.3 Verify Purchase
- Go to **Domain Management**
- You should see `safeascent.app` listed
- Status should be "Active"

> 📝 **Note:** Keep this tab open - you'll return to add DNS records later.

---

## Step 2: Set Up Database (Neon)

**Time: 10 minutes**

### 2.1 Create Neon Account
1. Go to [neon.tech](https://neon.tech)
2. Click **Sign Up** → **Continue with GitHub** (easiest)
3. Authorize Neon

### 2.2 Create Project
1. Click **Create Project**
2. Configure:
   - **Project name:** `safeascent`
   - **Database name:** `safeascent`
   - **Region:** `US East (Ohio)` (or closest to your users)
   - **Postgres version:** `16` (latest)
3. Click **Create Project**

### 2.3 Enable PostGIS Extension
1. In your project, go to **SQL Editor** (left sidebar)
2. Run this SQL:
```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```
3. Click **Run**
4. You should see: `CREATE EXTENSION` (success)

### 2.4 Get Connection String
1. Go to **Dashboard** (left sidebar)
2. Find **Connection string** section
3. Click the **copy icon** next to the connection string
4. It looks like:
```
postgresql://username:password@ep-xxxxx.us-east-2.aws.neon.tech/safeascent?sslmode=require
```

> ⚠️ **Important:** Save this connection string securely - you'll need it for Railway.

### 2.5 Convert to AsyncPG Format
SafeAscent uses async database connections. Modify the connection string:

**Original (from Neon):**
```
postgresql://username:password@ep-xxxxx.us-east-2.aws.neon.tech/safeascent?sslmode=require
```

**Modified (for SafeAscent):**
```
postgresql+asyncpg://username:password@ep-xxxxx.us-east-2.aws.neon.tech/safeascent?ssl=require
```

Changes:
- `postgresql://` → `postgresql+asyncpg://`
- `sslmode=require` → `ssl=require`

> 📝 Save both versions - you'll need the original for migrations and the asyncpg version for Railway.

---

## Step 3: Deploy to Railway

**Time: 20 minutes**

### 3.1 Create Railway Account
1. Go to [railway.app](https://railway.app)
2. Click **Login** → **Login with GitHub**
3. Authorize Railway
4. You get $5 free credit (no credit card needed initially)

### 3.2 Create New Project
1. Click **New Project**
2. Select **Empty Project**
3. Name it `safeascent`

### 3.3 Add Redis
1. Click **+ New** → **Database** → **Add Redis**
2. Wait for it to provision (30 seconds)
3. Click the Redis service → **Variables** tab
4. Note the `REDIS_URL` (Railway will auto-inject this)

### 3.4 Deploy Backend
1. Click **+ New** → **GitHub Repo**
2. Select your `SafeAscent` repository
3. Railway detects it's a monorepo - click **Add Service Anyway**
4. Click the new service → **Settings**
5. Configure:
   - **Service Name:** `backend`
   - **Root Directory:** `/backend`
   - **Builder:** `Dockerfile`
6. Go to **Variables** tab and add:

```bash
# Database (use your Neon asyncpg connection string)
DATABASE_URL=postgresql+asyncpg://username:password@ep-xxxxx.us-east-2.aws.neon.tech/safeascent?ssl=require

# Redis (use Railway's reference syntax)
REDIS_URL=${{Redis.REDIS_URL}}

# API Keys (replace with your actual keys)
OPENWEATHER_API_KEY=your_openweather_api_key_here

# CORS (we'll update this after getting the domain)
CORS_ORIGINS=https://safeascent.app,https://www.safeascent.app

# App Settings
DEBUG=false
USE_VECTORIZED_ALGORITHM=true
PROJECT_NAME=SafeAscent
API_V1_PREFIX=/api/v1
```

7. Go to **Settings** → **Networking** → **Generate Domain**
   - Note the generated URL (e.g., `backend-production-xxxx.up.railway.app`)

### 3.5 Deploy Frontend
1. Click **+ New** → **GitHub Repo**
2. Select `SafeAscent` again
3. Click **Add Service Anyway**
4. Click the new service → **Settings**
5. Configure:
   - **Service Name:** `frontend`
   - **Root Directory:** `/frontend`
   - **Builder:** `Dockerfile`
6. Go to **Variables** tab and add:

```bash
# API URL (will be your custom domain)
VITE_API_BASE_URL=https://api.safeascent.app/api/v1

# Mapbox (replace with your token)
VITE_MAPBOX_TOKEN=pk.your_mapbox_token_here
```

7. Go to **Settings** → **Networking** → **Generate Domain**
   - Note the generated URL (e.g., `frontend-production-xxxx.up.railway.app`)

### 3.6 Verify Services Are Running
1. Wait for both services to deploy (watch the **Deployments** tab)
2. Click the backend's Railway URL - you should see:
```json
{"message": "SafeAscent API", "version": "1.0.0", "docs": "/api/v1/docs"}
```
3. Click `/health` - you should see:
```json
{"status": "healthy"}
```

> ⚠️ If you see errors, check **Deployments** → **View Logs** for details.

---

## Step 4: Connect Custom Domain

**Time: 15 minutes**

### 4.1 Add Custom Domains in Railway

**For Frontend (main site):**
1. Click `frontend` service → **Settings** → **Networking**
2. Under **Custom Domain**, click **+ Custom Domain**
3. Enter: `safeascent.app`
4. Click **Add**
5. Railway shows you a CNAME target (e.g., `frontend-production-xxxx.up.railway.app`)
6. Repeat for `www.safeascent.app`

**For Backend (API):**
1. Click `backend` service → **Settings** → **Networking**
2. Under **Custom Domain**, click **+ Custom Domain**
3. Enter: `api.safeascent.app`
4. Click **Add**
5. Railway shows you a CNAME target

### 4.2 Configure DNS in Porkbun

1. Go to [porkbun.com](https://porkbun.com) → **Domain Management**
2. Click **DNS** next to `safeascent.app`
3. Delete any existing A or CNAME records for `@` or `www`
4. Add these records:

| Type | Host | Answer | TTL |
|------|------|--------|-----|
| CNAME | `@` | `frontend-production-xxxx.up.railway.app` | 600 |
| CNAME | `www` | `frontend-production-xxxx.up.railway.app` | 600 |
| CNAME | `api` | `backend-production-xxxx.up.railway.app` | 600 |

> 📝 Replace the `xxxx` with your actual Railway subdomains.

> ⚠️ Porkbun might not allow CNAME on root (`@`). If so, use their "ALIAS" record type instead, or enable **Porkbun URL Forwarding** to forward `@` to `www`.

### 4.3 Wait for DNS Propagation
- DNS changes take 5-30 minutes to propagate
- Check status at [dnschecker.org](https://dnschecker.org)
- Search for `safeascent.app` and verify CNAME records appear

### 4.4 Verify SSL Certificates
Railway automatically provisions SSL certificates. After DNS propagates:
1. Visit `https://safeascent.app` - should load frontend
2. Visit `https://api.safeascent.app/health` - should return `{"status": "healthy"}`

---

## Step 5: Migrate Data

**Time: 15-30 minutes**

### 5.1 Export from Local PostgreSQL
```bash
# On your local machine
pg_dump -Fc -h localhost -U postgres safeascent > safeascent_backup.dump
```

### 5.2 Import to Neon

**Option A: Using pg_restore (recommended)**
```bash
# Use the ORIGINAL Neon connection string (not asyncpg)
pg_restore -d "postgresql://username:password@ep-xxxxx.us-east-2.aws.neon.tech/safeascent?sslmode=require" safeascent_backup.dump
```

**Option B: Using Neon's Import Feature**
1. Go to Neon Dashboard → **Import**
2. Upload your dump file
3. Follow the wizard

### 5.3 Verify Data Migration
1. Go to Neon → **SQL Editor**
2. Run verification queries:
```sql
-- Check tables exist
SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';

-- Check data counts
SELECT 'accidents' as table_name, COUNT(*) as count FROM accidents
UNION ALL
SELECT 'mountains', COUNT(*) FROM mountains
UNION ALL
SELECT 'routes', COUNT(*) FROM routes;

-- Test PostGIS is working
SELECT PostGIS_Version();
```

---

## Step 6: Verify Deployment

### 6.1 Test All Endpoints
```bash
# Health check
curl https://api.safeascent.app/health

# API info
curl https://api.safeascent.app/

# List mountains
curl https://api.safeascent.app/api/v1/mountains?limit=5

# List routes
curl https://api.safeascent.app/api/v1/routes?limit=5
```

### 6.2 Test Frontend
1. Open `https://safeascent.app` in browser
2. Verify map loads
3. Try selecting a route and getting a prediction

### 6.3 Test Prediction (PostGIS)
```bash
curl -X POST https://api.safeascent.app/api/v1/predict \
  -H "Content-Type: application/json" \
  -d '{"route_id": 1, "date": "2026-02-15"}'
```

If this returns a risk score, PostGIS is working correctly!

---

## Troubleshooting

### Build Fails on Railway
**Check:** Dockerfile paths and root directory setting
```
Settings → Root Directory → should be `/backend` or `/frontend`
```

### Database Connection Error
**Check:** Connection string format
- Must use `postgresql+asyncpg://` prefix
- Must use `ssl=require` (not `sslmode=require`)

**Verify in Neon:**
```sql
SELECT 1;  -- Should return 1
```

### PostGIS Not Found
**Error:** `function st_dwithin does not exist`

**Fix:** Enable PostGIS in Neon:
```sql
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT PostGIS_Version();  -- Should return version
```

### CORS Errors
**Error:** `Access-Control-Allow-Origin` errors in browser console

**Fix:** Update `CORS_ORIGINS` in backend environment:
```
CORS_ORIGINS=https://safeascent.app,https://www.safeascent.app,http://localhost:5173
```

### SSL Certificate Pending
**Symptom:** Browser shows "Not Secure" warning

**Fix:** Wait 5-10 minutes after DNS propagation. Railway auto-provisions SSL via Let's Encrypt.

### Redis Connection Failed
**Check:** Verify Redis URL syntax in Railway:
```
REDIS_URL=${{Redis.REDIS_URL}}
```
The `${{...}}` syntax auto-injects the value from the Redis service.

---

## Environment Variables Reference

### Backend (Railway)
| Variable | Example | Required |
|----------|---------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host/db?ssl=require` | Yes |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Yes |
| `OPENWEATHER_API_KEY` | `abc123...` | Yes |
| `CORS_ORIGINS` | `https://safeascent.app,https://www.safeascent.app` | Yes |
| `DEBUG` | `false` | No (default: false) |
| `USE_VECTORIZED_ALGORITHM` | `true` | No (default: false) |

### Frontend (Railway)
| Variable | Example | Required |
|----------|---------|----------|
| `VITE_API_BASE_URL` | `https://api.safeascent.app/api/v1` | Yes |
| `VITE_MAPBOX_TOKEN` | `pk.eyJ1Ijoi...` | Yes |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         INTERNET                                │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PORKBUN DNS                                  │
│  safeascent.app     → frontend.railway.app                     │
│  api.safeascent.app → backend.railway.app                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌──────────────────┐            ┌──────────────────┐
│     RAILWAY      │            │     RAILWAY      │
│    Frontend      │            │     Backend      │
│  (Nginx + React) │            │    (FastAPI)     │
│    Port 80       │            │    Port 8000     │
└──────────────────┘            └────────┬─────────┘
                                         │
                          ┌──────────────┴──────────────┐
                          ▼                              ▼
                 ┌──────────────────┐          ┌──────────────────┐
                 │     RAILWAY      │          │      NEON        │
                 │      Redis       │          │ PostgreSQL+PostGIS│
                 │   (Caching)      │          │   (Database)     │
                 └──────────────────┘          └──────────────────┘
```

---

## Cost Summary

| Service | Provider | Monthly Cost |
|---------|----------|--------------|
| Frontend | Railway | ~$2 |
| Backend | Railway | ~$5 |
| Redis | Railway | ~$3 |
| PostgreSQL + PostGIS | Neon | $0 (free tier) |
| Domain | Porkbun | ~$0.90 |
| **Total** | | **~$11/month** |

---

## Next Steps After Deployment

1. **Set up monitoring:** Railway has built-in metrics, or add Sentry for error tracking
2. **Configure backups:** Neon has automatic point-in-time recovery
3. **Add Celery workers:** For background tasks (weather updates, cache warming)
4. **Set up CI/CD:** Already configured in `.github/workflows/ci.yml`
