# Two-View Map System

## Overview

The map now has **two distinct visualization modes** that users can toggle between:

1. **Cluster View** (Default) - Navigation mode with route aggregation
2. **Risk Coverage View** - Regional risk assessment with overlay

This design separates concerns: clustering is for navigation/overview, risk coverage is for safety analysis.

---

## View Modes

### 1. Cluster View (Navigation Mode)

**Purpose**: Quick navigation and route discovery

**What You See**:
- **Clusters**: Colored circles showing aggregated routes
  - Color = Average risk score of routes in cluster
  - Number = Count of routes in cluster
  - Click to zoom and expand
- **Individual Markers**: When zoomed in, unclustered routes appear
  - Colored by individual risk score
  - Click for detailed safety popup
- **Labels**: Route names visible at zoom 11+

**Best For**:
- Finding routes in an area
- Overview of climbing destinations
- Navigation between regions
- Quick route count assessment

**Technical Details**:
```javascript
<Source cluster={true} clusterRadius={30} clusterMaxZoom={16}>
  <Layer id="clusters" /> // Aggregated points
  <Layer id="unclustered-point" /> // Individual routes
  <Layer id="route-labels" /> // Names
</Source>
```

---

### 2. Risk Coverage View (Safety Analysis Mode)

**Purpose**: Visualize regional risk patterns

**What You See**:
- **Risk Coverage Overlay**: Semi-transparent colored regions
  - Each route paints a large circle colored by its risk score
  - Circles blend (not accumulate) to show area risk
  - Green areas = safe routes dominate
  - Red/orange areas = dangerous routes present
  - Continuous blanket coverage (like avalanche forecast maps)
- **Individual Route Dots**: All routes shown as small colored circles
  - Size scales with zoom (tiny at world view, normal when zoomed)
  - Always colored by risk score
  - Click for detailed safety popup
- **Labels**: Route names visible at zoom 13+ (higher threshold to reduce clutter)

**Best For**:
- Assessing regional safety conditions
- Comparing risk across different areas
- Planning climbing trips based on safety
- Understanding geographic risk distribution

**Key Visualization Principle**:
- **Colors BLEND, not ACCUMULATE**:
  - 200 safe routes (green) = green coverage ✅
  - 1 dangerous route (red) = red coverage ✅
  - Mix of safe + dangerous = yellow/orange ✅
- This is fundamentally different from heatmaps which sum values

**Technical Details**:
```javascript
<Source cluster={false}> // NO clustering
  <Layer id="risk-coverage" paint={{
    'circle-radius': 180px at zoom 8, // Very large
    'circle-opacity': 0.18, // Semi-transparent
    'circle-blur': 1.0, // Smooth gradients
    'circle-color': based on risk_score // Individual route's risk
  }} />
  <Layer id="individual-routes" /> // Small markers
  <Layer id="route-labels" minzoom={13} /> // Less clutter
</Source>
```

---

## UI Controls

**Toggle Location**: Top-left control panel, below date picker

**Toggle UI**:
```
┌─────────────────────────┐
│ 🗺️ Map View            │
├─────────────────────────┤
│ [Clusters] [Risk Coverage] │ ← Toggle buttons
├─────────────────────────┤
│ Navigation mode: route  │
│ clusters for quick      │
│ overview               │
└─────────────────────────┘
```

**Descriptions**:
- Cluster mode: "Navigation mode: route clusters for quick overview"
- Risk mode: "Risk mode: regional safety overlay with individual routes"

---

## How It Works

### Cluster View Logic

1. All routes loaded into clustered GeoJSON source
2. Mapbox automatically aggregates nearby routes based on zoom
3. Cluster properties calculate `risk_score_sum` (used for average color)
4. Clusters show at zoom 0-16, individual routes appear when expanded
5. Click cluster → zoom to expand → see individual routes

### Risk Coverage View Logic

1. All routes loaded into NON-clustered GeoJSON source
2. Two layers render:
   - **Background layer** (`risk-coverage`): Very large blurred circles
     - Radius: 180px at zoom 8 (continuous coverage)
     - Color: Based on route's individual `risk_score`
     - Opacity: 0.18 (semi-transparent for blending)
     - Blur: 1.0 (smooth gradients)
   - **Foreground layer** (`individual-routes`): Small route markers
     - Radius: 3px at zoom 8, scales up when zooming in
     - Color: Based on route's `color_code`
3. Circles overlap and blend to show regional patterns
4. No clustering = all 1,415 routes always visible (as dots or coverage)

---

## Performance Considerations

### Cluster View
- **Efficient**: Mapbox handles clustering natively
- **Scales well**: Only renders visible clusters/routes
- **Low memory**: Aggregates reduce render count

### Risk Coverage View
- **More intensive**: Renders all 1,415 routes as circles
- **Large circles**: Higher GPU usage for blur/blending
- **Still performant**: Mapbox GL handles it well
- **Recommendation**: Use for analysis, switch to cluster view for navigation

---

## User Workflow Examples

### Finding Routes to Climb
1. Start in **Cluster View** (default)
2. Browse map, clusters show route density
3. Click cluster to zoom in
4. Click individual route for safety details

### Assessing Regional Safety
1. Switch to **Risk Coverage View**
2. Zoom out to see regional patterns
3. Green areas = generally safe
4. Red/orange areas = higher risk conditions
5. Zoom in to see which specific routes contribute to risk

### Trip Planning
1. Use **Risk Coverage View** to identify safe regions
2. Switch to **Cluster View** to find specific routes in that region
3. Click routes to see detailed safety forecasts
4. Compare forecasts across different dates using date picker

---

## Visual Comparison

### Cluster View
```
Zoom 8 (default):
  🔵45  🟢12  🟠8   ← Clusters with counts

Zoom 14 (zoomed in):
  🟢 🟡 🟢 🔴 🟢    ← Individual markers
  El Cap  Half Dome ...
```

### Risk Coverage View
```
Zoom 8 (default):
  [Green glow over Yosemite Valley]
  [Orange glow over steep faces]
  [Red glow over ice routes]
  Tiny dots everywhere (all routes visible)

Zoom 14 (zoomed in):
  [Localized green/orange blending]
  Small colored dots become larger
  🟢 🟡 🟢 🔴 🟢
  Route names appear
```

---

## Console Output

### On Load
```
🗺️ Map loaded successfully
Default view mode: clusters
Toggle between Cluster and Risk Coverage views using the control panel
```

### Switching to Cluster View
```
🗺️ Switched to CLUSTER VIEW - Navigation mode with route aggregation
```

### Switching to Risk Coverage View
```
🎨 Switched to RISK COVERAGE VIEW - Regional risk overlay with individual routes
   → Each route paints a large colored circle based on its risk score
   → Colors blend (not accumulate) to show regional risk patterns
```

---

## Why Two Separate Views?

**Problem with Combined Approach**:
- Clustering and risk coverage have conflicting requirements
- Clusters aggregate data → can't show individual risk colors
- Risk coverage needs individual routes → breaks clustering
- Trying to do both creates flickering and complexity

**Two-View Solution**:
- Clear separation of concerns
- Each view optimized for its purpose
- No conflicts or flickering
- User chooses the right tool for their current task
- Simpler code, better performance

---

## Future Enhancements

Possible additions:
1. **Keyboard shortcuts**: 'C' for clusters, 'R' for risk
2. **URL state**: Remember user's preferred view
3. **Auto-switch**: Show risk view when zoomed out, clusters when zoomed in
4. **Legend**: Color scale explanation for risk coverage view
5. **Filters**: Show only routes above certain risk threshold in risk view

---

## Technical Notes

### Source Switching
- Mapbox requires separate `<Source>` components for cluster vs. non-cluster
- We conditionally render based on `mapViewMode` state
- Same GeoJSON data, different clustering config

### Layer IDs
- Cluster view: `clusters`, `unclustered-point`, `route-labels`
- Risk view: `risk-coverage`, `individual-routes`, `route-labels`
- Different layer IDs prevent conflicts when switching

### Interactive Layers
- Cluster view: `['unclustered-point', 'clusters']`
- Risk view: `['individual-routes']`
- Dynamically set based on active view

### Click Handlers
- Cluster click → zoom to expand
- Route click (either view) → show safety popup
- Handler checks `feature.layer.id` to determine behavior

---

## Success Criteria

✅ **Toggle works smoothly** - No errors when switching views
✅ **Cluster view shows aggregations** - Colored clusters with counts
✅ **Risk view shows coverage** - Continuous colored overlay
✅ **Risk colors reflect actual risk** - Not density
✅ **Individual routes clickable in both views** - Safety popup works
✅ **Performance acceptable** - No lag when switching
✅ **No flickering** - Stable rendering in both modes

---

This two-view system gives users the best of both worlds: efficient navigation through clustering and detailed risk analysis through coverage overlay! 🎯
