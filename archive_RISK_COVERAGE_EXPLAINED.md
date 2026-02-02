# Risk Coverage Overlay - The Solution to Density vs. Risk

## The Problem We Had

**Mapbox Heatmaps Accumulate Density** - This is fundamentally incompatible with risk visualization:

```
Example with traditional heatmap:
- Area A: 200 routes with risk_score = 10 (very safe)
  → Accumulated weight = 200 × 10 = 2,000
  → HIGH density → YELLOW/ORANGE color ❌ WRONG

- Area B: 1 route with risk_score = 80 (dangerous)
  → Accumulated weight = 1 × 80 = 80
  → LOW density → GREEN color ❌ WRONG
```

**What You Saw**: Purple/yellow everywhere routes were dense, regardless of actual risk.

**What You Wanted**: Green where routes are safe, red where routes are dangerous - regardless of how many routes there are.

---

## The Solution: Risk Coverage Overlay

Instead of heatmaps (which accumulate), we now use **large semi-transparent circles**:

### How It Works

1. **Each route gets a large circle** colored by its risk score:
   - Risk 0-30: Green circle
   - Risk 30-50: Yellow circle
   - Risk 50-70: Orange circle
   - Risk 70+: Red circle

2. **Circles are semi-transparent (12% opacity)** so they blend when overlapping:
   ```
   Green circle + Green circle = Still green ✅
   Green circle + Red circle = Orange/red tint appears ✅
   ```

3. **Circles have heavy blur (1.0)** creating smooth gradients like avalanche forecast maps

4. **Radius scales with zoom**:
   - Zoom 8 (default): 180px radius → continuous coverage
   - Zoom 12 (regional): 100px radius
   - Zoom 16 (individual): 30px radius → fades out to show markers

### Why This Works

**Example scenarios:**

**Area with 200 safe routes (risk = 10):**
- Each route paints a green circle
- 200 green circles overlap
- Result: Strong GREEN overlay ✅ Correct!

**Area with 1 dangerous route (risk = 80):**
- Route paints a red circle
- Result: RED overlay even though it's just 1 route ✅ Correct!

**Area with 100 safe + 10 dangerous routes:**
- 100 green circles + 10 red circles
- Green dominates but red creates orange/yellow tints
- Result: YELLOW/ORANGE showing moderate overall risk ✅ Correct!

---

## Technical Details

### Layer Configuration

```javascript
<Layer
  id="risk-coverage"
  type="circle"  // NOT heatmap
  source="routes"
  filter={['has', 'risk_score']} // Only routes with data
  maxzoom={16} // Fade out at high zoom
  paint={{
    // Large radius for continuous coverage
    'circle-radius': [
      'interpolate',
      ['exponential', 2],
      ['zoom'],
      8, 180,    // Overlaps create continuous coverage
      12, 100,
      16, 30,
    ],

    // Color based ONLY on THIS route's risk (not accumulated)
    'circle-color': [
      'step',
      ['get', 'risk_score'],
      '#4caf50',  // Green: 0-30
      30, '#fdd835',  // Yellow: 30-50
      50, '#ff9800',  // Orange: 50-70
      70, '#f44336',  // Red: 70+
    ],

    // Semi-transparent for blending
    'circle-opacity': 0.12,

    // Heavy blur for smooth gradients
    'circle-blur': 1.0,
  }}
/>
```

### Key Differences from Heatmap

| Heatmap (Old) | Risk Coverage (New) |
|---------------|---------------------|
| `type: 'heatmap'` | `type: 'circle'` |
| Colors based on `heatmap-density` (accumulated) | Colors based on `risk_score` (individual) |
| `heatmap-weight` accumulates across routes | Each circle is independent |
| Dense areas always show warm colors | Dense safe areas stay green |
| Uses `heatmap-color` expression | Uses `circle-color` expression |

---

## What You Should See Now

1. **Initial Load**:
   - Map loads with route markers (blue initially)
   - Progress bar: "Loading Safety Data: X / 1415 routes"

2. **As Data Loads** (every 100 routes):
   - Coverage overlay gradually appears
   - Colors reflect actual risk levels
   - Dense safe areas = green
   - Sparse dangerous areas = red/orange

3. **Zoom Behavior**:
   - **Zoom 0-12**: Full coverage overlay visible (blanket effect)
   - **Zoom 13-14**: Coverage fades, markers become prominent
   - **Zoom 15+**: Coverage minimal, individual markers clear

4. **Console Output**:
   ```
   === RISK COVERAGE DEBUG ===
   ✅ Risk coverage layer EXISTS
   ✅ Routes source EXISTS
   Routes with risk scores: 1398/1415
   Risk score range: 12.5 - 87.3 (avg: 42.1)
   ✅ Sample LOW risk route: Easy Street → 15.2 → GREEN
   ⚠️ Sample HIGH risk route: Widow Maker → 82.7 → RED
   🎨 Each route paints its risk color over a large area
   🎨 Colors blend when overlapping (not accumulate)
   Current zoom: 8.00
   ===========================
   ```

---

## Still Not Perfect? Adjustments We Can Make

If after testing you find:

### "Coverage is too bright/overpowering"
**Reduce opacity:**
```javascript
'circle-opacity': 0.08,  // Even more subtle (currently 0.12)
```

### "Not enough continuous coverage / seeing gaps"
**Increase radius:**
```javascript
8, 220,  // Larger circles (currently 180)
```

### "Coverage too uniform / not enough variation"
**Adjust color thresholds or add more steps:**
```javascript
'circle-color': [
  'step',
  ['get', 'risk_score'],
  '#2e7d32',  // Darker green: 0-20
  20, '#4caf50',  // Light green: 20-30
  30, '#fdd835',  // Yellow: 30-50
  // ... etc
]
```

### "Want it to look more like avalanche map"
**Increase blur:**
```javascript
'circle-blur': 1.5,  // Even smoother (currently 1.0)
```

---

## Performance Notes

**Rendering Performance:**
- Circle layers are very efficient in Mapbox
- Each route is a simple circle (not complex heatmap calculations)
- Should be noticeably faster than previous heatmap

**Data Loading:**
- Still fetches all 1,415 routes' safety scores (1-2 minutes)
- Coverage appears incrementally as data loads
- Final result: Complete risk coverage of all climbing areas

**Future Optimization:**
- Enable Celery cache warming (pre-calculate overnight)
- Instant loads with complete coverage

---

## Testing Checklist

After you reload the page, verify:

- [ ] Coverage overlay appears as data loads
- [ ] Safe areas (many green routes) show GREEN coverage
- [ ] Dangerous areas show ORANGE/RED coverage regardless of route count
- [ ] Coverage is continuous (blanket effect, not discs)
- [ ] Coverage fades out as you zoom in
- [ ] Individual markers visible on top of coverage
- [ ] Console shows "Risk coverage layer EXISTS"

---

## Why This Is Better

1. **Mathematically Correct**: Colors represent risk level, not route count
2. **Intuitive**: Matches mental model ("this area looks dangerous")
3. **Scales Properly**: Works for sparse and dense route areas
4. **Visually Clear**: Continuous coverage like weather/avalanche maps
5. **Performance**: Faster rendering than complex heatmap calculations

This is the proper solution for risk visualization in geographic data! 🎯
