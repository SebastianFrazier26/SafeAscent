# SafeAscent Deployment Guide

This guide walks you through deploying SafeAscent to Railway with a custom domain.

## Prerequisites

- GitHub account (with SafeAscent repo)
- Credit card for Railway ($5 credit free to start)
- ~$14/year for domain

---

## Part 1: Purchase Domain (safeascent.app)

### Option A: Cloudflare Registrar (Recommended)
1. Go to [dash.cloudflare.com](https://dash.cloudflare.com)
2. Create account or sign in
3. Click **Domain Registration** → **Register Domain**
4. Search for `safeascent.app`
5. Purchase (~$14/year, no markup)

### Option B: Porkbun (Alternative)
1. Go to [porkbun.com](https://porkbun.com)
2. Search for `safeascent.app`
3. Purchase (~$10.87 first year)

> 💡 **Tip:** Don't configure DNS yet - Railway will give you the records to add.

---

## Part 2: Deploy to Railway

### Step 1: Create Railway Account
1. Go to [railway.app](https://railway.app)
2. Click **Login** → **Login with GitHub**
3. Authorize Railway

### Step 2: Create New Project
1. Click **New Project**
2. Select **Deploy from GitHub repo**
3. Find and select `SafeAscent`
4. Railway will detect the monorepo structure

### Step 3: Add PostgreSQL Database
1. In your project, click **New** → **Database** → **Add PostgreSQL**
2. Railway automatically provisions it
3. Click the database → **Variables** tab
4. Copy the `DATABASE_URL` (you'll need this)

> ⚠️ **Important:** Railway's PostgreSQL doesn't include PostGIS by default.
> You have two options:
> - Use Railway's PostgreSQL for basic queries (works for most features)
> - Use [Neon](https://neon.tech) with PostGIS extension (free tier available)

### Step 4: Add Redis
1. Click **New** → **Database** → **Add Redis**
2. Copy the `REDIS_URL` from Variables tab

### Step 5: Configure Backend Service
1. Click **New** → **GitHub Repo** → Select SafeAscent
2. Configure:
   - **Root Directory:** `backend`
   - **Build Command:** (auto-detected from Dockerfile)

3. Add Environment Variables (Settings → Variables):
```
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
DEBUG=false
USE_VECTORIZED_ALGORITHM=true
CORS_ORIGINS=https://safeascent.app,https://www.safeascent.app
OPENWEATHER_API_KEY=<your-api-key>
```

4. Generate domain: Settings → Networking → Generate Domain
   - Note: This gives you `backend-production-xxxx.up.railway.app`

### Step 6: Configure Frontend Service
1. Click **New** → **GitHub Repo** → Select SafeAscent again
2. Configure:
   - **Root Directory:** `frontend`

3. Add Environment Variables:
```
VITE_API_BASE_URL=https://api.safeascent.app/api/v1
VITE_MAPBOX_TOKEN=<your-mapbox-token>
```

4. Generate domain for frontend too

### Step 7: Set Up Custom Domains

**For the frontend (main site):**
1. Frontend service → Settings → Networking → Custom Domain
2. Add: `safeascent.app`
3. Add: `www.safeascent.app`
4. Railway shows you the CNAME records to add

**For the backend (API):**
1. Backend service → Settings → Networking → Custom Domain
2. Add: `api.safeascent.app`
3. Railway shows you the CNAME record

### Step 8: Configure DNS (Cloudflare/Porkbun)

Add these DNS records:

| Type | Name | Target |
|------|------|--------|
| CNAME | `@` | `<railway-frontend-domain>.up.railway.app` |
| CNAME | `www` | `<railway-frontend-domain>.up.railway.app` |
| CNAME | `api` | `<railway-backend-domain>.up.railway.app` |

> 💡 DNS propagation takes 5-30 minutes. Be patient!

---

## Part 3: Verify Deployment

### Check Services
```bash
# Health check
curl https://api.safeascent.app/health

# API docs
open https://api.safeascent.app/docs

# Frontend
open https://safeascent.app
```

### Monitor Logs
- Railway Dashboard → Click service → **Deployments** → **View Logs**

---

## Environment Variables Reference

### Backend
| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `OPENWEATHER_API_KEY` | Yes | For weather forecasts |
| `CORS_ORIGINS` | Yes | Comma-separated allowed origins |
| `DEBUG` | No | Set to `false` in production |
| `USE_VECTORIZED_ALGORITHM` | No | `true` for faster predictions |

### Frontend
| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_API_BASE_URL` | Yes | Backend API URL |
| `VITE_MAPBOX_TOKEN` | Yes | Mapbox access token |

---

## Cost Breakdown

| Service | Estimated Monthly Cost |
|---------|----------------------|
| Backend (FastAPI) | $5-7 |
| Frontend (Nginx) | $0-2 |
| PostgreSQL | $5-7 |
| Redis | $3-5 |
| **Total** | **~$15-20/month** |
| Domain (.app) | ~$1.17/month ($14/year) |

---

## Troubleshooting

### Build Fails
- Check Dockerfile paths are correct
- Ensure all dependencies in requirements.txt

### Database Connection Error
- Verify `DATABASE_URL` is set correctly
- Check if using `${{Postgres.DATABASE_URL}}` syntax

### CORS Errors
- Add your domain to `CORS_ORIGINS`
- Include both `https://safeascent.app` and `https://www.safeascent.app`

### Frontend Can't Reach API
- Verify `VITE_API_BASE_URL` points to `https://api.safeascent.app/api/v1`
- Check backend is running and healthy

---

## Automatic Deployments

Railway automatically deploys when you push to `main`. To disable:
1. Service → Settings → Source
2. Disable "Auto Deploy"

---

## Database Migration

To load your existing data:

1. Get your Railway PostgreSQL connection string
2. Use `pg_dump` and `pg_restore`:

```bash
# Export from local
pg_dump -Fc safeascent > safeascent.dump

# Import to Railway (use connection string from Railway)
pg_restore -d "postgresql://postgres:xxx@xxx.railway.app:5432/railway" safeascent.dump
```

Or use Railway's built-in data import in the database dashboard.
