# Heatmap & Cluster Visualization - Explained

## What Was Wrong and How It's Fixed

### Issue #1: Heatmap Not Visible ❌ → ✅ FIXED

**Problem**: The heatmap layer was rendering but invisible because routes lacked `risk_score` data.

**How Heatmaps Work**:
- Heatmaps are **blurred gradient overlays** (like weather radar) that show regional patterns
- Different from clusters (discrete circular markers)
- Uses Mapbox `heatmap` layer type with properties:
  - `heatmap-weight`: Determined by risk_score (higher = more heat)
  - `heatmap-radius`: Area of influence (~50km at zoom 8)
  - `heatmap-color`: Green (safe) → yellow → orange → red (dangerous)

**Why It Wasn't Showing**:
```javascript
// Heatmap weight depends on risk_score
'heatmap-weight': ['/', ['get', 'risk_score'], 80]
```
- Without `risk_score`, weight = 0 → no visible heat
- Originally only fetched 500/1415 routes (35%)
- Routes don't have risk_score in database - must be fetched from API

**Fix**:
- Now fetches ALL 1,415 routes' safety scores on load
- Takes 1-2 minutes but provides complete coverage
- Heatmap appears as data loads (incremental updates every 100 routes)

**What You'll See**:
- Colored "glow" effect showing regional risk concentrations
- More visible at zoom levels 0-12 (regional view)
- Fades out at zoom 13-14 (individual marker view)

---

### Issue #2: Clusters Showing Gray ❌ → ✅ FIXED

**Problem**: Clusters were gray because routes within them lacked `risk_score` data.

**How Cluster Colors Work**:
```javascript
clusterProperties: {
  risk_score_sum: ['+', ['coalesce', ['get', 'risk_score'], 0]]
}

'circle-color': [
  'case',
  ['>', ['get', 'risk_score_sum'], 0],
  ['step', ['/', ['get', 'risk_score_sum'], ['get', 'point_count']],
    '#4caf50',  // Green: avg 0-30
    30, '#fdd835',  // Yellow: avg 30-50
    50, '#ff9800',  // Orange: avg 50-70
    70, '#f44336',  // Red: avg 70+
  ],
  '#9e9e9e'  // Gray: no risk data (sum = 0)
]
```

**Why Clusters Were Gray**:
1. Mapbox aggregates `risk_score` across all routes in cluster → `risk_score_sum`
2. If most routes don't have risk_score, sum = 0
3. Sum = 0 triggers gray "no data" color

**Fix**:
- Now fetches ALL routes → all routes get risk_score
- Clusters show average risk score of all routes in the cluster
- Cluster labels show the average score number (not route count)

**What You'll See**:
- Clusters colored green/yellow/orange/red based on average safety
- Number inside cluster = average risk score (not count)
- Gray only appears briefly while data loads

---

### Issue #3: Route Names Incorrect When Clicking ℹ️ EXPECTED BEHAVIOR

**This is NOT a bug** - it's expected behavior due to coordinate overlap.

**What's Happening**:
```sql
-- Database query results:
35.4495, -82.2126: 29 routes
  Names: ['Frosted Flake', 'Shredded Wheat', 'Breakfast of Champions', ...]

35.2095, -81.2981: 6 routes
  Names: ['Red Wall', 'patrolled,', 'It's Crowded up there.']
```

**Why This Happens**:
- Multiple routes share the exact same GPS coordinates
- This is normal for routes on the same cliff face/wall
- Mapbox renders all routes at the same location, stacked on top of each other

**Label Behavior**:
- Mapbox picks ONE route to show the label for (collision avoidance)
- When you click, you might get a DIFFERENT route that's at the same coordinates
- Both routes ARE at that location - the label just shows one of them

**Example**:
```
Location: 35.4495, -82.2126
- Label shows: "Frosted Flake"
- You click: Get popup for "Shredded Wheat"
- Both routes are ACTUALLY at (35.4495, -82.2126)
```

**Not a Bug**: This is how geographic data works when multiple features share coordinates.

---

## New Loading Experience

**Progress Indicator**:
- Shows under date picker while loading
- Displays: "Loading Safety Data: 523 / 1415 routes (37%)"
- Progress bar for visual feedback
- Message: "Heatmap & cluster colors will appear as data loads"

**Timeline**:
1. **0-5 seconds**: Route markers load (all blue initially)
2. **5-60 seconds**: Safety scores fetch (10 routes/batch, ~140 batches)
3. **Every 100 routes**: Map updates incrementally (visual feedback)
4. **1-2 minutes**: Complete - heatmap visible, clusters colored, markers colored

**Console Logs**:
```
🎯 Fetching safety scores for ALL 1415 routes on 2026-02-01...
⏳ This will take 1-2 minutes but enables full heatmap + cluster colors
✅ Loaded safety scores for 1398/1415 routes
```

---

## Performance Notes

**Why Not Pre-Load Everything?**
- Current approach: Fetch on-demand (1-2 min wait)
- Alternative: Celery cache warming (runs every 6 hours automatically)
  - Warms cache for top 200 routes × 7 days = 1,400 calculations
  - These routes load instantly (< 10ms from Redis cache)
  - Remaining routes still need on-demand calculation

**Future Optimization**:
- Run Celery worker + beat to enable background cache warming
- Pre-calculate ALL routes overnight
- Instant page loads with complete data

**To Start Cache Warming**:
```bash
# Terminal 1: Start Celery worker
cd backend
./run_celery_worker.sh

# Terminal 2: Start Celery beat scheduler
cd backend
./run_celery_beat.sh
```

---

## Visual Examples

### Heatmap (Regional View - Zoom 8)
```
🟢🟢🟢🟢🟢 ← Green glow (safe areas)
  🟡🟡🟡   ← Yellow (moderate risk)
    🟠    ← Orange (elevated risk)
     🔴   ← Red (high risk)
```
- Smooth gradient transitions
- Shows density + risk level
- Visible behind markers/clusters

### Clusters (Close-up View - Zoom 10-12)
```
  🟢25  ← Green cluster: avg risk score 25
  🟡42  ← Yellow cluster: avg risk score 42
  🟠61  ← Orange cluster: avg risk score 61
  🔴85  ← Red cluster: avg risk score 85
```

### Individual Markers (Zoomed In - Zoom 13+)
```
🟢 El Capitan
🟡 Half Dome
🟠 The Nose
🔴 Free Rider
```
- Colored circles with names below
- Click for detailed safety popup

---

## Troubleshooting

### Heatmap Still Not Visible?
1. **Check zoom level**: Heatmap is most visible at zoom 0-12
2. **Wait for data load**: Progress bar shows when data is loading
3. **Check console**: Look for "✅ Loaded safety scores for X/1415 routes"
4. **Zoom out**: Heatmap fades at zoom 13-14 (designed behavior)

### Clusters Still Gray?
1. **Wait for progress bar**: Data loading
2. **Check specific cluster**: Zoom in to see if individual routes are colored
3. **Console errors?**: Check browser console for API errors

### Route Name Mismatch?
- **Expected behavior** - multiple routes at same coordinates
- Check route name in popup (authoritative)
- Label is just one of many routes at that location

---

## Data Quality Note

Some route names may appear corrupted (e.g., "Lleida McKinley '96" - an accident report title). This is a known data issue from the Mountain Project scraper and will be fixed in a future data cleanup phase. For now, the focus is on building the UI to completion.
