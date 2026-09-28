/**
 * MapView Component
 *
 * Interactive Mapbox GL map displaying climbing routes and regional risk information.
 * Features: clickable route markers, safety scores, date-based filtering
 */
import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { Map as MapGL, NavigationControl, ScaleControl, Source, Layer, Popup } from 'react-map-gl';
import {
  Box, Paper, Typography, CircularProgress, Dialog, DialogTitle,
  DialogContent, DialogActions, Button, Chip, Divider, ToggleButtonGroup, ToggleButton
} from '@mui/material';
import { DatePicker } from '@mui/x-date-pickers/DatePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import { addDays, startOfToday, format } from 'date-fns';
import 'mapbox-gl/dist/mapbox-gl.css';
import RouteAnalyticsModal from './RouteAnalyticsModal';
import RiskLegend from './RiskLegend';
import { useRouteSafety } from '../hooks/useRouteSafety';
import {
  NO_RISK_HEX,
  NO_RISK_TEXT_HEX,
  RISK_BAND_THRESHOLDS,
  RISK_COLOR_HEX,
  RISK_TEXT_ON_HEX,
  routeSafetyProps,
} from '../utils/riskUtils';
import { hexToRgba, mixHex } from '../utils/color';

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

const [RISK_LOW_MAX, RISK_MODERATE_MAX, RISK_HIGH_MAX] = RISK_BAND_THRESHOLDS;
// Heatmap layers overlap by this many points either side of each band edge so adjacent
// colours blend. It is a rendering overlap only; markers/clusters/legend use exact edges.
const HEATMAP_BLEND = 2;
// 'gray' is what the map endpoint sends for a route with no score.
const ROUTE_COLOR_MATCH_ARMS = [
  ...Object.entries(RISK_COLOR_HEX).flat(),
  'gray', NO_RISK_HEX,
];
// Cluster fill and its count label pick from parallel band tables on the same average score,
// so the label colour always matches the fill it sits on.
const clusterBandColor = (byBand, none) => [
  'case',
  ['>', ['get', 'risk_score_count'], 0],
  [
    'step',
    ['/', ['get', 'risk_score_sum'], ['get', 'risk_score_count']],
    byBand.green,
    RISK_LOW_MAX, byBand.yellow,
    RISK_MODERATE_MAX, byBand.orange,
    RISK_HIGH_MAX, byBand.red,
  ],
  none,
];
// Heatmap ramps come from the band palette so the heatmap cannot drift from the markers.
// Edges and peaks lean toward the neighbouring band's hue so overlapping layers blend.
const BLACK = '#000000';
const heatRamp = (stops) => [
  'interpolate', ['linear'], ['heatmap-density'],
  ...stops.flatMap(([density, hex, alpha]) => [density, hexToRgba(hex, alpha)]),
];
const HEATMAP_COLOR = {
  base: heatRamp([
    [0, BLACK, 0],
    [0.05, NO_RISK_HEX, 0.25],
    [0.3, NO_RISK_HEX, 0.35],
    [1, NO_RISK_HEX, 0.4],
  ]),
  low: heatRamp([
    [0, RISK_COLOR_HEX.green, 0],
    [0.05, RISK_COLOR_HEX.green, 0.4],
    [0.2, RISK_COLOR_HEX.green, 0.6],
    [0.5, RISK_COLOR_HEX.green, 0.7],
    [1, mixHex(RISK_COLOR_HEX.green, RISK_COLOR_HEX.yellow, 0.4), 0.75],
  ]),
  moderate: heatRamp([
    [0, RISK_COLOR_HEX.yellow, 0],
    [0.05, mixHex(RISK_COLOR_HEX.yellow, RISK_COLOR_HEX.green, 0.25), 0.4],
    [0.2, RISK_COLOR_HEX.yellow, 0.65],
    [0.5, RISK_COLOR_HEX.yellow, 0.8],
    [1, mixHex(RISK_COLOR_HEX.yellow, RISK_COLOR_HEX.orange, 0.5), 0.85],
  ]),
  elevated: heatRamp([
    [0, RISK_COLOR_HEX.orange, 0],
    [0.05, mixHex(RISK_COLOR_HEX.orange, RISK_COLOR_HEX.yellow, 0.3), 0.5],
    [0.2, RISK_COLOR_HEX.orange, 0.7],
    [0.5, RISK_COLOR_HEX.orange, 0.85],
    [1, mixHex(RISK_COLOR_HEX.orange, RISK_COLOR_HEX.red, 0.7), 0.9],
  ]),
  high: heatRamp([
    [0, RISK_COLOR_HEX.red, 0],
    [0.05, mixHex(RISK_COLOR_HEX.red, RISK_COLOR_HEX.orange, 0.3), 0.55],
    [0.2, RISK_COLOR_HEX.red, 0.75],
    [0.5, RISK_COLOR_HEX.red, 0.9],
    [1, mixHex(RISK_COLOR_HEX.red, BLACK, 0.25), 0.95],
  ]),
};
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

// Default view: Centered on Rocky Mountains (major climbing destination)
const INITIAL_VIEW_STATE = {
  latitude: 40.0150,  // Rocky Mountain National Park
  longitude: -105.2705,
  zoom: 8,
  pitch: 45, // 3D tilt
  bearing: 0,
};

// Map styles for different seasons
const MAP_STYLES = {
  default: 'mapbox://styles/mapbox/outdoors-v12',      // Topographic - good for summer/all
  summer: 'mapbox://styles/mapbox/outdoors-v12',       // Topographic with trails
  winter: 'mapbox://styles/sebfrazi/cml76s6gt009s01s3aue5ghjk', // Custom Outdoors Winter theme
};

/**
 * MapView - Main interactive map component
 */
export default function MapView({ selectedRouteForZoom }) {
  const mapRef = useRef();
  const [viewState, setViewState] = useState(INITIAL_VIEW_STATE);

  // Selected date for safety calculations (default: today)
  const today = startOfToday();
  const [selectedDate, setSelectedDate] = useState(today);
  const maxDate = addDays(today, 2); // 3-day window

  // Routes data
  const [routes, setRoutes] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Selected route for detail popup
  const [selectedRoute, setSelectedRoute] = useState(null);
  const { state: safetyState, retry: retrySafety } = useRouteSafety(
    selectedRoute?.properties?.id ?? null,
    format(selectedDate, 'yyyy-MM-dd'),
  );

  const okSafety = safetyState?.status === 'ok' ? safetyState.data : null;
  const insufficientSafety = safetyState?.status === 'insufficient';
  // Memoized so the modal's props don't change on every map pan/hover render. Keyed on
  // okSafety, not safetyState, since the hook derives a fresh loading object each render.
  const modalRouteData = useMemo(() => (selectedRoute ? {
      route_id: selectedRoute.properties.id,
      name: selectedRoute.properties.name,
      mountain_name: selectedRoute.properties.mountain_name || 'Unknown Mountain',
      type: selectedRoute.properties.type,
      grade: selectedRoute.properties.grade,
      latitude: selectedRoute.geometry.coordinates[1],
      longitude: selectedRoute.geometry.coordinates[0],
      elevation_meters: null,
      risk_score: okSafety ? okSafety.risk_score : null,
      color_code: okSafety ? okSafety.color_code : insufficientSafety ? 'gray' : null,
      data_status: okSafety ? 'ok' : insufficientSafety ? 'insufficient_data' : null,
      mp_route_id: selectedRoute.properties.mp_route_id,
    } : null), [selectedRoute, okSafety, insufficientSafety]);

  // Track safety score loading progress (now just for display, bulk fetch is fast)
  const [safetyLoadingProgress, setSafetyLoadingProgress] = useState({ loaded: 0, total: 0, isLoading: false });

  // Map view mode: 'clusters' (navigation) or 'risk' (risk coverage overlay)
  const [mapViewMode, setMapViewMode] = useState('clusters');

  // Hover state for showing route names on hover
  const [hoveredRoute, setHoveredRoute] = useState(null);

  // Season filter: 'rock' (default) or 'winter' (ice/mixed routes)
  const [seasonFilter, setSeasonFilter] = useState('rock');

  // Track current zoom level for conditional heatmap rendering
  const [_currentZoom, setCurrentZoom] = useState(INITIAL_VIEW_STATE.zoom);
  const HEATMAP_MIN_ZOOM = 6; // Only show heatmap when zoomed in past this level

  // Map style changes are handled by the key prop on MapGL component
  // This forces a full remount when style changes, avoiding race conditions

  /**
   * Routes are now filtered server-side via the `season` query parameter.
   * This useMemo just passes through the data, but is kept for potential
   * future client-side filtering needs.
   */
  const filteredRoutes = useMemo(() => {
    // Server-side filtering handles rock vs ice/mixed
    // Boulder routes have been deleted from the database
    return routes;
  }, [routes]);

  /**
   * Compute map style based on season filter
   * Winter uses satellite imagery to show actual snow coverage
   */
  const currentMapStyle = useMemo(() => {
    if (seasonFilter === 'winter') {
      return MAP_STYLES.winter;
    }
    return MAP_STYLES.default;
  }, [seasonFilter]);


  /**
   * Fetch routes WITH pre-computed safety scores in a single request.
   * Uses the bulk endpoint /mp-routes/map-with-safety which returns routes
   * with safety scores already embedded - no need for individual API calls!
   */
  useEffect(() => {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    const fetchJsonWithRetry = async (url, attempts = 3) => {
      let lastError = null;
      for (let i = 0; i < attempts; i++) {
        try {
          const response = await fetch(url);
          if (!response.ok) {
            throw new Error(`Failed to fetch routes: ${response.status} ${response.statusText}`);
          }
          return await response.json();
        } catch (error) {
          lastError = error;
          if (i < attempts - 1) {
            await sleep((i + 1) * 800);
          }
        }
      }
      throw lastError;
    };

    const fetchRoutesWithSafety = async () => {
      try {
        // Clear old routes immediately to force clean re-render on season change
        setRoutes(null);
        setLoading(true);
        setSafetyLoadingProgress({ loaded: 0, total: 0, isLoading: true });

        // Format date for API
        const dateStr = format(selectedDate, 'yyyy-MM-dd');

        let data = null;
        try {
          // BULK ENDPOINT: Returns routes WITH safety scores in single request
          // This replaces 168K individual safety API calls with 1 request.
          data = await fetchJsonWithRetry(
            `${API_BASE_URL}/mp-routes/map-with-safety?target_date=${dateStr}&season=${seasonFilter}`,
            3
          );
          const { meta } = data;
          console.log(`📊 Bulk fetch stats: ${meta.cached_routes}/${meta.total_routes} cached, ${meta.missing_routes} missing`);
        } catch (bulkError) {
          console.warn('Bulk safety fetch failed; falling back to routes-only endpoint.', bulkError);
          // Fallback keeps routes visible even if safety payload has transient network issues.
          data = await fetchJsonWithRetry(
            `${API_BASE_URL}/mp-routes/map?season=${seasonFilter}`,
            2
          );
        }

        // Group routes by coordinates to handle overlapping points
        const coordMap = new Map();

        (data.routes || []).forEach((route) => {
          const coordKey = `${route.latitude.toFixed(6)},${route.longitude.toFixed(6)}`;

          if (!coordMap.has(coordKey)) {
            coordMap.set(coordKey, []);
          }
          coordMap.get(coordKey).push(route);
        });

        // Convert to GeoJSON format with auto-spacing for overlapping routes
        const features = [];
        let overlappingRouteCount = 0;
        let routesWithSafety = 0;

        coordMap.forEach((routesAtLocation, _coordKey) => {
          if (routesAtLocation.length === 1) {
            // Single route - use original coordinates
            const route = routesAtLocation[0];
            const safetyProps = routeSafetyProps(route.safety);
            if (safetyProps.data_status !== null) routesWithSafety++;

            features.push({
              type: 'Feature',
              geometry: {
                type: 'Point',
                coordinates: [route.longitude, route.latitude],
              },
              properties: {
                id: route.mp_route_id,
                name: route.name,
                grade: route.grade || 'N/A',
                type: normalizeRouteTypeForDisplay(route.type),
                mp_route_id: route.mp_route_id,
                location_id: route.location_id,
                // Insufficient-data and unscored routes are gray with no score, so the
                // cluster average (risk_score_sum / risk_score_count) leaves them out.
                ...safetyProps,
              },
            });
          } else {
            // Multiple routes at same location - arrange in tight grid cluster
            overlappingRouteCount += routesAtLocation.length;
            const numRoutes = routesAtLocation.length;

            const baseOffset = 0.00004; // ~4.4 meters at equator
            const cols = Math.ceil(Math.sqrt(numRoutes));
            const rows = Math.ceil(numRoutes / cols);

            const gridWidth = (cols - 1) * baseOffset;
            const gridHeight = (rows - 1) * baseOffset;
            const startLon = routesAtLocation[0].longitude - gridWidth / 2;
            const startLat = routesAtLocation[0].latitude - gridHeight / 2;

            routesAtLocation.forEach((route, index) => {
              const col = index % cols;
              const row = Math.floor(index / cols);
              const offsetLon = startLon + col * baseOffset;
              const offsetLat = startLat + row * baseOffset;

              const safetyProps = routeSafetyProps(route.safety);
              if (safetyProps.data_status !== null) routesWithSafety++;

              features.push({
                type: 'Feature',
                geometry: {
                  type: 'Point',
                  coordinates: [offsetLon, offsetLat],
                },
                properties: {
                  id: route.mp_route_id,
                  name: route.name,
                  grade: route.grade || 'N/A',
                  type: normalizeRouteTypeForDisplay(route.type),
                  mp_route_id: route.mp_route_id,
                  location_id: route.location_id,
                  ...safetyProps,
                },
              });
            });
          }
        });

        const geojson = {
          type: 'FeatureCollection',
          features: features,
        };

        console.log(`✅ Loaded ${geojson.features.length} ${seasonFilter} routes with ${routesWithSafety} safety scores`);
        if (overlappingRouteCount > 0) {
          console.log(`📍 Auto-spaced ${overlappingRouteCount} overlapping routes`);
        }

        setRoutes(geojson);
        setError(null);
        setSafetyLoadingProgress({ loaded: routesWithSafety, total: features.length, isLoading: false });
      } catch (err) {
        console.error('Error fetching routes:', err);
        setError(err.message);
        setSafetyLoadingProgress({ loaded: 0, total: 0, isLoading: false });
      } finally {
        setLoading(false);
      }
    };

    fetchRoutesWithSafety();
  }, [seasonFilter, selectedDate]); // Re-fetch when season or date changes

  /**
   * Helper: Check if a route type is ice/mixed (winter season)
   */
  const isWinterRouteType = (routeType) => {
    if (!routeType) return false;
    const normalized = routeType.toLowerCase();
    return normalized.includes('ice') || normalized.includes('mixed');
  };

  /**
   * Handle zoom to route or mountain from search
   * Auto-switches season if route type doesn't match current filter
   */
  useEffect(() => {
    if (!selectedRouteForZoom) return;

    const map = mapRef.current?.getMap();

    // Handle location/area selection
    if (selectedRouteForZoom.resultType === 'location' || selectedRouteForZoom.type === 'location') {
      // Zoom to location
      if (map && selectedRouteForZoom.latitude && selectedRouteForZoom.longitude) {
        map.easeTo({
          center: [selectedRouteForZoom.longitude, selectedRouteForZoom.latitude],
          zoom: 12, // Zoom to see mountain area with routes
          duration: 1000,
        });
      }
      return;
    }

    // Handle route selection - check if we need to switch seasons
    const routeType = selectedRouteForZoom.routeType || selectedRouteForZoom.type;
    const isWinterRoute = isWinterRouteType(routeType);
    const needsSeasonSwitch = (isWinterRoute && seasonFilter === 'rock') ||
                              (!isWinterRoute && seasonFilter === 'winter');

    if (needsSeasonSwitch) {
      // Switch to the correct season - route data will reload via useEffect
      console.log(`🔄 Auto-switching season: ${routeType} route requires ${isWinterRoute ? 'winter' : 'rock'} season`);
      setSeasonFilter(isWinterRoute ? 'winter' : 'rock');
      // Don't zoom yet - wait for routes to reload (handled in next render)
      return;
    }

    // Routes are already loaded for correct season - find and zoom to route
    if (!routes) return;

    const routeFeature = routes.features.find(
      (f) => f.properties.id === selectedRouteForZoom.route_id
    );

    if (routeFeature && map) {
      // Zoom to route
      map.easeTo({
        center: routeFeature.geometry.coordinates,
        zoom: 14, // Close zoom to see individual route
        duration: 1000,
      });

      // Open route details after zoom
      setTimeout(() => {
        setSelectedRoute(routeFeature);
      }, 500);
    } else if (!routeFeature) {
      console.log(`⚠️ Route ${selectedRouteForZoom.route_id} not found in current map data`);
    }
  }, [selectedRouteForZoom, routes, seasonFilter]);

  /**
   * Log when map view mode changes
   */
  useEffect(() => {
    if (mapViewMode === 'clusters') {
      console.log('🗺️ Switched to CLUSTER VIEW - Navigation mode with route aggregation');
    } else {
      console.log('🎨 Switched to RISK COVERAGE VIEW - Stratified heatmap with smooth blending');
      console.log('   → Base: Gray heatmap shows ALL climbing areas (contrast for non-climbing areas)');
      console.log('   → Risk layers with overlapping boundaries for smooth transitions:');
      console.log(`   → Bands ${RISK_BAND_THRESHOLDS.join('/')}, heatmap overlap ±${HEATMAP_BLEND}`);
      console.log('   → Smaller radius (70px) for tighter coverage');
      console.log('   → No gray in Oklahoma/central US = no climbing routes there');
    }
  }, [mapViewMode]);

  /**
   * Handle click on route marker or cluster
   */
  const handleMarkerClick = useCallback((event) => {
    const feature = event.features?.[0];
    if (!feature) return;

    // Handle cluster click - zoom in to expand
    if (feature.layer.id === 'clusters') {
      const clusterId = feature.properties.cluster_id;
      const mapboxMap = mapRef.current?.getMap();
      const source = mapboxMap?.getSource('routes');

      if (source && 'getClusterExpansionZoom' in source) {
        source.getClusterExpansionZoom(clusterId, (err, zoom) => {
          if (err) return;

          mapboxMap.easeTo({
            center: feature.geometry.coordinates,
            zoom: zoom + 0.5, // Zoom in slightly more for better visualization
            duration: 500,
          });
        });
      }
      return;
    }

    // Handle individual route click - show details
    if (feature.layer.id === 'unclustered-point' || feature.layer.id === 'individual-routes') {
      setSelectedRoute(feature);
    }
  }, []);

  /**
   * Handle mouse move for hover tooltips
   */
  const handleMouseMove = useCallback((event) => {
    const feature = event.features?.[0];
    if (!feature) {
      setHoveredRoute(null);
      return;
    }

    // Show hover tooltip for route markers (not clusters)
    if (feature.layer.id === 'unclustered-point' || feature.layer.id === 'individual-routes') {
      setHoveredRoute({
        name: feature.properties.name,
        coordinates: feature.geometry.coordinates,
      });
    } else {
      setHoveredRoute(null);
    }
  }, []);

  /**
   * Handle mouse leave - clear hover state
   */
  const handleMouseLeave = useCallback(() => {
    setHoveredRoute(null);
  }, []);

  /**
   * Handle map load - verify heatmap layer
   */
  const handleMapLoad = useCallback(() => {
    const map = mapRef.current?.getMap();
    if (!map) return;

    // Log map info after load
    console.log('🗺️ Map loaded successfully');
    console.log(`Default view mode: ${mapViewMode}`);
    console.log('Toggle between Cluster and Risk Coverage views using the control panel');
  }, [mapViewMode]);


  return (
    <LocalizationProvider dateAdapter={AdapterDateFns}>
      <Box sx={{ position: 'relative', width: '100%', height: '100%' }}>
        <MapGL
          key={currentMapStyle}
          ref={mapRef}
          {...viewState}
          onMove={(evt) => {
            setViewState(evt.viewState);
            setCurrentZoom(evt.viewState.zoom);
          }}
          onLoad={handleMapLoad}
          onClick={handleMarkerClick}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          interactiveLayerIds={
            mapViewMode === 'clusters'
              ? ['unclustered-point', 'clusters']
              : ['individual-routes']
          }
          mapboxAccessToken={MAPBOX_TOKEN}
          mapStyle={currentMapStyle}
          style={{ width: '100%', height: '100%' }}
          cursor="pointer"
        >
          {/* Navigation controls */}
          <NavigationControl position="top-right" />
          <ScaleControl position="bottom-right" />

          {/* CLUSTER VIEW MODE - Navigation with route aggregation */}
          {routes && mapViewMode === 'clusters' && (
            <Source
              key={`routes-clusters-${seasonFilter}`}
              id="routes"
              type="geojson"
              data={filteredRoutes}
              cluster={true}
              clusterMaxZoom={16}
              clusterRadius={30}
              clusterProperties={{
                risk_score_sum: ['+', ['coalesce', ['get', 'risk_score'], 0]],
                // Unscored routes must not count toward the average, or they drag it greener.
                risk_score_count: ['+', ['case', ['==', ['typeof', ['get', 'risk_score']], 'number'], 1, 0]],
              }}
            >
              {/* Clustered points - color by average safety score */}
              <Layer
                id="clusters"
                type="circle"
                source="routes"
                filter={['has', 'point_count']}
                paint={{
                  'circle-color': clusterBandColor(RISK_COLOR_HEX, NO_RISK_HEX),
                  'circle-radius': [
                    'step',
                    ['get', 'point_count'],
                    15, 100, 22, 750, 30,
                  ],
                  'circle-stroke-width': 1.5,
                  'circle-stroke-color': '#fff',
                }}
              />

              {/* Cluster labels */}
              <Layer
                id="cluster-count"
                type="symbol"
                source="routes"
                filter={['has', 'point_count']}
                layout={{
                  'text-field': ['get', 'point_count_abbreviated'],
                  'text-font': ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
                  'text-size': 14,
                }}
                paint={{
                  'text-color': clusterBandColor(RISK_TEXT_ON_HEX, NO_RISK_TEXT_HEX),
                }}
              />

              {/* Individual unclustered markers */}
              <Layer
                id="unclustered-point"
                type="circle"
                source="routes"
                filter={['!', ['has', 'point_count']]}
                paint={{
                  'circle-color': [
                    'match',
                    ['get', 'color_code'],
                    ...ROUTE_COLOR_MATCH_ARMS,
                    '#11b4da'
                  ],
                  'circle-radius': 6,
                  'circle-stroke-width': 1.5,
                  'circle-stroke-color': '#fff',
                }}
              />

              {/* Route labels */}
              <Layer
                id="route-labels"
                type="symbol"
                source="routes"
                filter={['!', ['has', 'point_count']]}
                minzoom={11}
                layout={{
                  'text-field': ['get', 'name'],
                  'text-font': ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
                  'text-size': 11,
                  'text-variable-anchor': ['top', 'bottom', 'left', 'right', 'top-left', 'top-right', 'bottom-left', 'bottom-right'],
                  'text-radial-offset': 1.2,
                  'text-justify': 'auto',
                  'text-max-width': 10,
                  'text-allow-overlap': false,
                  'text-optional': true,
                }}
                paint={{
                  'text-color': '#2c3e50',
                  'text-halo-color': '#ffffff',
                  'text-halo-width': 2,
                  'text-halo-blur': 1,
                }}
              />
            </Source>
          )}

          {/* RISK COVERAGE VIEW MODE - Regional risk overlay with all individual routes */}
          {/* Heatmap "heat" is always visible for overview; individual dots only when zoomed in */}
          {routes && mapViewMode === 'risk' && (
            <Source
              key={`routes-risk-${seasonFilter}`}
              id="routes"
              type="geojson"
              data={filteredRoutes}
              cluster={false}  // NO clustering in risk view
            >
              {/* Layered Heatmap Approach - One heatmap per risk category */}
              {/* Each layer shows smooth density for routes in that risk bracket */}
              {/* Higher risk layers render on top for correct visual priority */}
              {/* Heatmap layers visible at ALL zoom levels for wide-zoom overview */}

              {/* BASE LAYER: Gray heatmap showing ALL climbing areas */}
              {/* Provides contrast - gray = climbing data exists, no gray = no climbing data */}
              <Layer
                id="climbing-coverage-base"
                type="heatmap"
                source="routes"
                // No filter - ALL routes contribute to gray base
                paint={{
                  'heatmap-weight': 1,
                  'heatmap-radius': [
                    'interpolate', ['exponential', 1.5], ['zoom'],
                    0, 25, 4, 40, 6, 55, 8, 70, 10, 55, 12, 40, 14, 25, 16, 12,
                  ],
                  'heatmap-intensity': 1.0,
                  'heatmap-color': HEATMAP_COLOR.base,
                  'heatmap-opacity': 0.7,
                }}
              />

              {/* Layer 1: LOW RISK - Green heatmap (band + HEATMAP_BLEND overlap) */}
              <Layer
                id="risk-low"
                type="heatmap"
                source="routes"
                filter={['all', ['has', 'risk_score'], ['<', ['get', 'risk_score'], RISK_LOW_MAX + HEATMAP_BLEND]]}
                paint={{
                  'heatmap-weight': 1,
                  'heatmap-radius': [
                    'interpolate', ['exponential', 1.5], ['zoom'],
                    0, 25, 4, 40, 6, 55, 8, 70, 10, 55, 12, 40, 14, 25, 16, 12,
                  ],
                  'heatmap-intensity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 1.0, 6, 1.2, 10, 1.4,
                  ],
                  'heatmap-color': HEATMAP_COLOR.low,
                  'heatmap-opacity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 0.8, 8, 0.85, 12, 0.75, 14, 0.6, 16, 0.3,
                  ],
                }}
              />

              {/* Layer 2: MODERATE RISK - Yellow heatmap */}
              <Layer
                id="risk-moderate"
                type="heatmap"
                source="routes"
                filter={['all', ['>=', ['get', 'risk_score'], RISK_LOW_MAX - HEATMAP_BLEND], ['<', ['get', 'risk_score'], RISK_MODERATE_MAX + HEATMAP_BLEND]]}
                paint={{
                  'heatmap-weight': 1,
                  'heatmap-radius': [
                    'interpolate', ['exponential', 1.5], ['zoom'],
                    0, 25, 4, 40, 6, 55, 8, 70, 10, 55, 12, 40, 14, 25, 16, 12,
                  ],
                  'heatmap-intensity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 1.0, 6, 1.2, 10, 1.4,
                  ],
                  'heatmap-color': HEATMAP_COLOR.moderate,
                  'heatmap-opacity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 0.8, 8, 0.85, 12, 0.75, 14, 0.6, 16, 0.3,
                  ],
                }}
              />

              {/* Layer 3: ELEVATED RISK - Orange heatmap */}
              <Layer
                id="risk-elevated"
                type="heatmap"
                source="routes"
                filter={['all', ['>=', ['get', 'risk_score'], RISK_MODERATE_MAX - HEATMAP_BLEND], ['<', ['get', 'risk_score'], RISK_HIGH_MAX + HEATMAP_BLEND]]}
                paint={{
                  'heatmap-weight': 1,
                  'heatmap-radius': [
                    'interpolate', ['exponential', 1.5], ['zoom'],
                    0, 25, 4, 40, 6, 55, 8, 70, 10, 55, 12, 40, 14, 25, 16, 12,
                  ],
                  'heatmap-intensity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 1.0, 6, 1.2, 10, 1.4,
                  ],
                  'heatmap-color': HEATMAP_COLOR.elevated,
                  'heatmap-opacity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 0.8, 8, 0.85, 12, 0.75, 14, 0.6, 16, 0.3,
                  ],
                }}
              />

              {/* Layer 4: HIGH RISK - Red heatmap */}
              <Layer
                id="risk-high"
                type="heatmap"
                source="routes"
                filter={['>=', ['get', 'risk_score'], RISK_HIGH_MAX - HEATMAP_BLEND]}
                paint={{
                  'heatmap-weight': 1,
                  'heatmap-radius': [
                    'interpolate', ['exponential', 1.5], ['zoom'],
                    0, 25, 4, 40, 6, 55, 8, 70, 10, 55, 12, 40, 14, 25, 16, 12,
                  ],
                  'heatmap-intensity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 1.0, 6, 1.2, 10, 1.4,
                  ],
                  'heatmap-color': HEATMAP_COLOR.high,
                  'heatmap-opacity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 0.8, 8, 0.85, 12, 0.75, 14, 0.6, 16, 0.3,
                  ],
                }}
              />

              {/* Individual route markers - only visible when zoomed in */}
              {/* The 160K dots would overwhelm the view at wide zooms */}
              <Layer
                id="individual-routes"
                type="circle"
                source="routes"
                minzoom={HEATMAP_MIN_ZOOM}
                paint={{
                  'circle-color': [
                    'match',
                    ['get', 'color_code'],
                    ...ROUTE_COLOR_MATCH_ARMS,
                    '#11b4da'
                  ],
                  // Smaller markers in risk view to avoid clutter
                  'circle-radius': [
                    'interpolate',
                    ['linear'],
                    ['zoom'],
                    0, 1,     // Tiny at world view
                    6, 2,
                    8, 3,     // Small at default
                    12, 5,
                    16, 7,    // Normal size when zoomed in
                  ],
                  'circle-stroke-width': 1,
                  'circle-stroke-color': '#fff',
                }}
              />

              {/* Route labels - show at same zoom as cluster view */}
              <Layer
                id="route-labels"
                type="symbol"
                source="routes"
                minzoom={11}  // Same as cluster view
                layout={{
                  'text-field': ['get', 'name'],
                  'text-font': ['DIN Offc Pro Medium', 'Arial Unicode MS Bold'],
                  'text-size': 10,
                  'text-variable-anchor': ['top', 'bottom', 'left', 'right'],
                  'text-radial-offset': 1.0,
                  'text-justify': 'auto',
                  'text-max-width': 10,
                  'text-allow-overlap': false,
                  'text-optional': true,
                }}
                paint={{
                  'text-color': '#2c3e50',
                  'text-halo-color': '#ffffff',
                  'text-halo-width': 2,
                  'text-halo-blur': 1,
                }}
              />
            </Source>
          )}

          {/* Hover Tooltip - Shows route name on hover */}
          {hoveredRoute && (
            <Popup
              longitude={hoveredRoute.coordinates[0]}
              latitude={hoveredRoute.coordinates[1]}
              closeButton={false}
              closeOnClick={false}
              anchor="bottom"
              offset={10}
              style={{
                pointerEvents: 'none',  // Don't interfere with mouse events
              }}
            >
              <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.875rem' }}>
                {hoveredRoute.name}
              </Typography>
            </Popup>
          )}
        </MapGL>

        {/* Date Picker - Controls safety score date */}
        <Paper
          elevation={3}
          sx={{
            position: 'absolute',
            top: 16,
            left: 16,
            p: 1.25,  // More compact
            zIndex: 1,
            bgcolor: 'background.paper',
            borderRadius: 2,
            maxWidth: 240,
          }}
        >
          <Typography variant="body2" fontWeight={600} gutterBottom sx={{ mb: 0.75, fontSize: '0.85rem' }}>
            📅 Forecast Date
          </Typography>
          <DatePicker
            value={selectedDate}
            onChange={(newDate) => setSelectedDate(newDate)}
            minDate={today}
            maxDate={maxDate}
            slotProps={{
              textField: {
                size: 'small',
                helperText: '3-day forecast',
                sx: {
                  width: 210,
                  '& .MuiInputBase-root': {
                    fontSize: '0.875rem',
                  },
                  '& .MuiFormHelperText-root': {
                    fontSize: '0.65rem',
                    marginTop: '2px',
                  }
                }
              }
            }}
          />

          <Divider sx={{ my: 1.25 }} />

          <Typography variant="body2" fontWeight={600} gutterBottom sx={{ mb: 0.75, fontSize: '0.85rem' }}>
            🗺️ Map View
          </Typography>
          <ToggleButtonGroup
            value={mapViewMode}
            exclusive
            onChange={(event, newMode) => {
              if (newMode !== null) {
                setMapViewMode(newMode);
              }
            }}
            size="small"
            fullWidth
            sx={{
              mb: 0.5,
              '& .MuiToggleButton-root': {
                fontSize: '0.75rem',
                py: 0.5,
              }
            }}
          >
            <ToggleButton value="clusters">
              Clusters
            </ToggleButton>
            <ToggleButton value="risk">
              Risk Coverage
            </ToggleButton>
          </ToggleButtonGroup>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.65rem', lineHeight: 1.25 }}>
            {mapViewMode === 'clusters'
              ? 'Cluster navigation mode'
              : 'Regional risk overlay mode'}
          </Typography>

          <Divider sx={{ my: 1.25 }} />

          <Typography variant="body2" fontWeight={600} gutterBottom sx={{ mb: 0.75, fontSize: '0.85rem' }}>
            🧗 Route Season
          </Typography>
          <ToggleButtonGroup
            value={seasonFilter}
            exclusive
            onChange={(event, newFilter) => {
              if (newFilter !== null) {
                setSeasonFilter(newFilter);
              }
            }}
            size="small"
            fullWidth
            sx={{
              mb: 0.5,
              '& .MuiToggleButton-root': {
                fontSize: '0.8rem',
                py: 0.5,
                px: 2,
              }
            }}
          >
            <ToggleButton value="rock">
              🪨 Rock
            </ToggleButton>
            <ToggleButton value="winter">
              ❄️ Ice
            </ToggleButton>
          </ToggleButtonGroup>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.65rem', lineHeight: 1.25 }}>
            {seasonFilter === 'rock'
              ? 'Rock routes (alpine, trad, sport, aid)'
              : 'Ice & mixed routes'}
          </Typography>
        </Paper>

        {/* Safety Score Loading Progress */}
        {safetyLoadingProgress.isLoading && (
          <Paper
            elevation={3}
            sx={{
              position: 'absolute',
              top: 290,  // Moved further down to avoid overlap
              left: 16,
              p: 1.25,  // More compact
              zIndex: 1,
              bgcolor: 'info.50',
              borderRadius: 2,
              border: 1,
              borderColor: 'info.200',
              maxWidth: 240,
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
              <CircularProgress size={16} thickness={5} />
              <Typography variant="body2" fontWeight={600} sx={{ fontSize: '0.85rem' }}>
                Loading Safety Data
              </Typography>
            </Box>
            {safetyLoadingProgress.total > 0 ? (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5, fontSize: '0.7rem' }}>
                {safetyLoadingProgress.loaded} / {safetyLoadingProgress.total} routes
                ({Math.round((safetyLoadingProgress.loaded / safetyLoadingProgress.total) * 100)}%)
              </Typography>
            ) : (
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5, fontSize: '0.7rem' }}>
                Fetching routes...
              </Typography>
            )}
            <Box sx={{
              width: '100%',
              height: 4,
              bgcolor: 'grey.200',
              borderRadius: 1,
              overflow: 'hidden',
              mb: 0.5,
            }}>
              <Box sx={{
                width: safetyLoadingProgress.total > 0
                  ? `${(safetyLoadingProgress.loaded / safetyLoadingProgress.total) * 100}%`
                  : '10%',
                height: '100%',
                bgcolor: 'info.main',
                transition: 'width 0.3s ease',
              }} />
            </Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontSize: '0.65rem', lineHeight: 1.25 }}>
              💡 Colors appear as data loads
            </Typography>
          </Paper>
        )}

        {/* Note: Main loading indicator removed - map shows its own loading state */}

        {/* Error message */}
        {error && (
          <Paper
            elevation={3}
            sx={{
              position: 'absolute',
              top: 16,
              left: '50%',
              transform: 'translateX(-50%)',
              px: 3,
              py: 2,
              bgcolor: 'error.light',
              color: 'error.contrastText',
            }}
          >
            <Typography variant="body2">
              ⚠️ Error loading routes: {error}
            </Typography>
          </Paper>
        )}

        {/* Route count indicator */}
        {routes && !loading && (
          <Paper
            elevation={2}
            sx={{
              position: 'absolute',
              top: 16,
              left: '50%',
              transform: 'translateX(-50%)',
              px: 2,
              py: 0.5,
              bgcolor: 'rgba(255, 255, 255, 0.95)',
            }}
          >
            <Typography variant="caption" color="text.secondary">
              📍 <Box component="span" fontWeight={600}>
                {filteredRoutes?.features?.length?.toLocaleString() || 0}
              </Box> {seasonFilter === 'rock' ? 'rock' : 'ice'} routes loaded
            </Typography>
          </Paper>
        )}

        <RiskLegend />

        {/* Mapbox attribution (required) */}
        <Paper
          sx={{
            position: 'absolute',
            bottom: 8,
            left: 8,
            px: 1,
            py: 0.5,
            pointerEvents: 'none',
            bgcolor: 'rgba(255, 255, 255, 0.8)',
          }}
        >
          <Typography variant="caption" color="text.secondary">
            © Mapbox | © OpenStreetMap
          </Typography>
        </Paper>
      </Box>

      {/* Route Analytics Modal */}
      <RouteAnalyticsModal
        open={!!selectedRoute}
        onClose={() => setSelectedRoute(null)}
        routeData={modalRouteData}
        selectedDate={format(selectedDate, 'yyyy-MM-dd')}
        safety={safetyState}
        onRetrySafety={retrySafety}
      />
    </LocalizationProvider>
  );
}

/**
 * Normalize route type for display
 * Converts "YDS" (grading system) to actual route types
 */
function normalizeRouteTypeForDisplay(routeType) {
  if (!routeType || routeType === 'unknown') {
    return 'Trad/Sport';
  }

  const normalized = routeType.toLowerCase().trim();

  // YDS is a grading system, not a route type
  if (normalized === 'yds') {
    return 'Trad/Sport';
  }

  // Map common variations
  const typeMap = {
    'traditional': 'Trad',
    'sport climb': 'Sport',
    'ice climb': 'Ice',
    'ice climbing': 'Ice',
    'alpine climb': 'Alpine',
    'mountaineering': 'Alpine',
    'bouldering': 'Boulder',
    'big_wall': 'Big Wall',
    'aid climb': 'Aid',
  };

  if (typeMap[normalized]) {
    return typeMap[normalized];
  }

  // Capitalize first letter for known types
  const knownTypes = ['trad', 'sport', 'alpine', 'ice', 'mixed', 'aid', 'boulder'];
  if (knownTypes.includes(normalized)) {
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  }

  return 'Trad/Sport'; // Default fallback
}
