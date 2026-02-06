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

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;
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
  const maxDate = addDays(today, 6); // 7-day window

  // Routes data
  const [routes, setRoutes] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Selected route for detail popup
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [safetyData, setSafetyData] = useState(null);
  const [_loadingSafety, setLoadingSafety] = useState(false);

  // Track safety score loading progress (now just for display, bulk fetch is fast)
  const [safetyLoadingProgress, setSafetyLoadingProgress] = useState({ loaded: 0, total: 0, isLoading: false });

  // Map view mode: 'clusters' (navigation) or 'risk' (risk coverage overlay)
  const [mapViewMode, setMapViewMode] = useState('clusters');

  // Hover state for showing route names on hover
  const [hoveredRoute, setHoveredRoute] = useState(null);

  // Season filter: 'rock' (default) or 'winter' (ice/mixed routes)
  const [seasonFilter, setSeasonFilter] = useState('rock');

  // Track current zoom level for conditional heatmap rendering
  const [currentZoom, setCurrentZoom] = useState(INITIAL_VIEW_STATE.zoom);
  const HEATMAP_MIN_ZOOM = 6; // Only show heatmap when zoomed in past this level

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
    const fetchRoutesWithSafety = async () => {
      try {
        setLoading(true);
        setSafetyLoadingProgress({ loaded: 0, total: 0, isLoading: true });

        // Format date for API
        const dateStr = format(selectedDate, 'yyyy-MM-dd');

        // BULK ENDPOINT: Returns routes WITH safety scores in single request
        // This replaces 168K individual safety API calls with 1 request!
        const response = await fetch(
          `${API_BASE_URL}/mp-routes/map-with-safety?target_date=${dateStr}&season=${seasonFilter}`
        );
        if (!response.ok) {
          throw new Error(`Failed to fetch routes: ${response.statusText}`);
        }
        const data = await response.json();

        // Log metadata about cache status
        const { meta } = data;
        console.log(`📊 Bulk fetch stats: ${meta.cached_routes}/${meta.total_routes} cached, ${meta.missing_routes} missing`);

        // Group routes by coordinates to handle overlapping points
        const coordMap = new Map();

        data.routes.forEach((route) => {
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
            const hasSafety = route.safety !== null;
            if (hasSafety) routesWithSafety++;

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
                // Safety scores embedded from bulk response!
                color_code: hasSafety ? route.safety.color_code : 'gray',
                risk_score: hasSafety ? route.safety.risk_score : null,
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

              const hasSafety = route.safety !== null;
              if (hasSafety) routesWithSafety++;

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
                  // Safety scores embedded from bulk response!
                  color_code: hasSafety ? route.safety.color_code : 'gray',
                  risk_score: hasSafety ? route.safety.risk_score : null,
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
   * Handle zoom to route or mountain from search
   */
  useEffect(() => {
    if (!selectedRouteForZoom || !routes) return;

    const map = mapRef.current?.getMap();
    if (!map) return;

    // Handle location/area selection (previously called 'mountain')
    if (selectedRouteForZoom.type === 'location') {
      // Zoom to location
      if (selectedRouteForZoom.latitude && selectedRouteForZoom.longitude) {
        map.easeTo({
          center: [selectedRouteForZoom.longitude, selectedRouteForZoom.latitude],
          zoom: 12, // Zoom to see mountain area with routes
          duration: 1000,
        });
      }
      return;
    }

    // Handle route selection
    const routeFeature = routes.features.find(
      (f) => f.properties.id === selectedRouteForZoom.route_id
    );

    if (routeFeature) {
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
    }
  }, [selectedRouteForZoom, routes]);

  /**
   * Fetch safety score for selected route
   */
  useEffect(() => {
    if (!selectedRoute) {
      setSafetyData(null);
      return;
    }

    const fetchSafety = async () => {
      try {
        setLoadingSafety(true);
        const dateStr = format(selectedDate, 'yyyy-MM-dd');
        const response = await fetch(
          `${API_BASE_URL}/mp-routes/${selectedRoute.properties.id}/safety?target_date=${dateStr}`,
          { method: 'POST' }
        );

        if (!response.ok) {
          throw new Error(`Failed to fetch safety score: ${response.statusText}`);
        }

        const data = await response.json();
        setSafetyData(data);
      } catch (err) {
        console.error('Error fetching safety score:', err);
        setSafetyData({ error: err.message });
      } finally {
        setLoadingSafety(false);
      }
    };

    fetchSafety();
  }, [selectedRoute, selectedDate]);

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
      console.log('     • Green: 0-32 (low risk)');
      console.log('     • Yellow: 28-52 (moderate) ← overlaps green & orange');
      console.log('     • Orange: 48-72 (elevated) ← overlaps yellow & red');
      console.log('     • Red: 68+ (high risk) ← overlaps orange');
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
              id="routes"
              type="geojson"
              data={filteredRoutes}
              cluster={true}
              clusterMaxZoom={16}
              clusterRadius={30}
              clusterProperties={{
                risk_score_sum: ['+', ['coalesce', ['get', 'risk_score'], 0]],
              }}
            >
              {/* Clustered points - color by average safety score */}
              <Layer
                id="clusters"
                type="circle"
                source="routes"
                filter={['has', 'point_count']}
                paint={{
                  'circle-color': [
                    'case',
                    ['>', ['get', 'risk_score_sum'], 0],
                    [
                      'step',
                      ['/', ['get', 'risk_score_sum'], ['get', 'point_count']],
                      '#4caf50',  // Green: 0-30
                      30, '#fdd835',  // Yellow: 30-50
                      50, '#ff9800',  // Orange: 50-70
                      70, '#f44336',  // Red: 70+
                    ],
                    '#9e9e9e'  // Gray: no data
                  ],
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
                  'text-color': '#ffffff',
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
                    'green', '#4caf50',
                    'yellow', '#fdd835',
                    'orange', '#ff9800',
                    'red', '#f44336',
                    'gray', '#9e9e9e',
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
          {/* Only load data when zoomed in enough to prevent 160K point performance issues */}
          {routes && mapViewMode === 'risk' && currentZoom >= HEATMAP_MIN_ZOOM && (
            <Source
              id="routes"
              type="geojson"
              data={filteredRoutes}
              cluster={false}  // NO clustering in risk view
            >
              {/* Layered Heatmap Approach - One heatmap per risk category */}
              {/* Each layer shows smooth density for routes in that risk bracket */}
              {/* Higher risk layers render on top for correct visual priority */}

              {/* BASE LAYER: Gray heatmap showing ALL climbing areas */}
              {/* Provides contrast - gray = climbing data exists, no gray = no climbing data */}
              <Layer
                id="climbing-coverage-base"
                type="heatmap"
                source="routes"
                minzoom={HEATMAP_MIN_ZOOM}
                // No filter - ALL routes contribute to gray base
                paint={{
                  'heatmap-weight': 1,
                  'heatmap-radius': [
                    'interpolate', ['exponential', 1.5], ['zoom'],
                    0, 25, 4, 40, 6, 55, 8, 70, 10, 55, 12, 40, 14, 25, 16, 12,
                  ],
                  'heatmap-intensity': 1.0,
                  'heatmap-color': [
                    'interpolate', ['linear'], ['heatmap-density'],
                    0, 'rgba(0, 0, 0, 0)',             // Transparent where no routes
                    0.05, 'rgba(158, 158, 158, 0.25)', // Light gray shows climbing areas
                    0.3, 'rgba(158, 158, 158, 0.35)',
                    1, 'rgba(158, 158, 158, 0.4)',
                  ],
                  'heatmap-opacity': 0.7,
                }}
              />

              {/* Layer 1: LOW RISK (0-32) - Green heatmap */}
              {/* Extended to 32 to create overlap zone with yellow for smoother blending */}
              <Layer
                id="risk-low"
                type="heatmap"
                source="routes"
                minzoom={HEATMAP_MIN_ZOOM}
                filter={['all', ['has', 'risk_score'], ['<', ['get', 'risk_score'], 32]]}
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
                  'heatmap-color': [
                    'interpolate', ['linear'], ['heatmap-density'],
                    0, 'rgba(76, 175, 80, 0)',        // Transparent far from routes
                    0.05, 'rgba(76, 175, 80, 0.4)',   // Soft green at edges
                    0.2, 'rgba(76, 175, 80, 0.6)',    // Green
                    0.5, 'rgba(76, 175, 80, 0.7)',
                    1, 'rgba(139, 195, 74, 0.75)',    // Light green at peak (blends toward yellow)
                  ],
                  'heatmap-opacity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 0.8, 8, 0.85, 12, 0.75, 14, 0.6, 16, 0.3,
                  ],
                }}
              />

              {/* Layer 2: MODERATE RISK (28-52) - Yellow heatmap */}
              {/* Overlaps with green (28-32) and orange (48-52) for smooth transitions */}
              <Layer
                id="risk-moderate"
                type="heatmap"
                source="routes"
                minzoom={HEATMAP_MIN_ZOOM}
                filter={['all', ['>=', ['get', 'risk_score'], 28], ['<', ['get', 'risk_score'], 52]]}
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
                  'heatmap-color': [
                    'interpolate', ['linear'], ['heatmap-density'],
                    0, 'rgba(253, 216, 53, 0)',
                    0.05, 'rgba(205, 220, 57, 0.4)',  // Yellow-green transition at edges
                    0.2, 'rgba(253, 216, 53, 0.65)',  // Yellow
                    0.5, 'rgba(253, 216, 53, 0.8)',
                    1, 'rgba(255, 193, 7, 0.85)',     // Amber at peak (blends toward orange)
                  ],
                  'heatmap-opacity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 0.8, 8, 0.85, 12, 0.75, 14, 0.6, 16, 0.3,
                  ],
                }}
              />

              {/* Layer 3: ELEVATED RISK (48-72) - Orange heatmap */}
              {/* Overlaps with yellow (48-52) and red (68-72) for smooth transitions */}
              <Layer
                id="risk-elevated"
                type="heatmap"
                source="routes"
                minzoom={HEATMAP_MIN_ZOOM}
                filter={['all', ['>=', ['get', 'risk_score'], 48], ['<', ['get', 'risk_score'], 72]]}
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
                  'heatmap-color': [
                    'interpolate', ['linear'], ['heatmap-density'],
                    0, 'rgba(255, 152, 0, 0)',
                    0.05, 'rgba(255, 171, 0, 0.5)',   // Orange-yellow at edges
                    0.2, 'rgba(255, 152, 0, 0.7)',    // Orange
                    0.5, 'rgba(255, 152, 0, 0.85)',
                    1, 'rgba(255, 87, 34, 0.9)',      // Deep orange at peak (blends toward red)
                  ],
                  'heatmap-opacity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 0.8, 8, 0.85, 12, 0.75, 14, 0.6, 16, 0.3,
                  ],
                }}
              />

              {/* Layer 4: HIGH RISK (68+) - Red heatmap */}
              {/* Overlaps with orange (68-72) for smooth transition */}
              <Layer
                id="risk-high"
                type="heatmap"
                source="routes"
                minzoom={HEATMAP_MIN_ZOOM}
                filter={['>=', ['get', 'risk_score'], 68]}
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
                  'heatmap-color': [
                    'interpolate', ['linear'], ['heatmap-density'],
                    0, 'rgba(244, 67, 54, 0)',
                    0.05, 'rgba(255, 87, 34, 0.55)',  // Red-orange at edges
                    0.2, 'rgba(244, 67, 54, 0.75)',   // Red
                    0.5, 'rgba(244, 67, 54, 0.9)',
                    1, 'rgba(183, 28, 28, 0.95)',     // Dark red at peak
                  ],
                  'heatmap-opacity': [
                    'interpolate', ['linear'], ['zoom'],
                    0, 0.8, 8, 0.85, 12, 0.75, 14, 0.6, 16, 0.3,
                  ],
                }}
              />

              {/* Individual route markers - smaller in risk view */}
              <Layer
                id="individual-routes"
                type="circle"
                source="routes"
                paint={{
                  'circle-color': [
                    'match',
                    ['get', 'color_code'],
                    'green', '#4caf50',
                    'yellow', '#fdd835',
                    'orange', '#ff9800',
                    'red', '#f44336',
                    'gray', '#9e9e9e',
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

        {/* Zoom in prompt for Risk Coverage mode */}
        {mapViewMode === 'risk' && currentZoom < HEATMAP_MIN_ZOOM && (
          <Paper
            elevation={4}
            sx={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              p: 3,
              zIndex: 10,
              bgcolor: 'background.paper',
              borderRadius: 3,
              textAlign: 'center',
              maxWidth: 320,
              boxShadow: '0 8px 32px rgba(0,0,0,0.15)',
            }}
          >
            <Typography variant="h6" fontWeight={700} gutterBottom>
              🔍 Zoom In to View Risk Heatmap
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              The risk coverage heatmap displays best at closer zoom levels.
              Zoom in to a specific region to see the risk overlay.
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Current zoom: {currentZoom.toFixed(1)} • Required: {HEATMAP_MIN_ZOOM}+
            </Typography>
          </Paper>
        )}

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
                helperText: '7-day forecast',
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
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5, fontSize: '0.7rem' }}>
              {safetyLoadingProgress.loaded} / {safetyLoadingProgress.total} routes
              ({Math.round((safetyLoadingProgress.loaded / safetyLoadingProgress.total) * 100)}%)
            </Typography>
            <Box sx={{
              width: '100%',
              height: 4,
              bgcolor: 'grey.200',
              borderRadius: 1,
              overflow: 'hidden',
              mb: 0.5,
            }}>
              <Box sx={{
                width: `${(safetyLoadingProgress.loaded / safetyLoadingProgress.total) * 100}%`,
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

        {/* Loading indicator */}
        {loading && (
          <Paper
            elevation={3}
            sx={{
              position: 'absolute',
              top: 16,
              left: '50%',
              transform: 'translateX(-50%)',
              px: 3,
              py: 2,
              display: 'flex',
              alignItems: 'center',
              gap: 2,
              bgcolor: 'background.paper',
            }}
          >
            <CircularProgress size={20} />
            <Typography variant="body2">
              Loading {routes?.features?.length || 0} routes...
            </Typography>
          </Paper>
        )}

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

        {/* Safety Gradient Legend */}
        <Paper
          elevation={3}
          sx={{
            position: 'absolute',
            bottom: 40,
            left: 16,
            p: 2,
            zIndex: 1,
            bgcolor: 'background.paper',
            borderRadius: 2,
            minWidth: 200,
          }}
        >
          <Typography variant="subtitle2" fontWeight={600} gutterBottom sx={{ mb: 1.5 }}>
            🎯 Safety Score Legend
          </Typography>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {/* Green - Safe */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  bgcolor: '#4caf50',
                  border: '2px solid #fff',
                  boxShadow: 1,
                }}
              />
              <Box>
                <Typography variant="body2" fontWeight={500}>
                  Safe (0-30)
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Favorable conditions
                </Typography>
              </Box>
            </Box>

            {/* Yellow - Moderate */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  bgcolor: '#fdd835',
                  border: '2px solid #fff',
                  boxShadow: 1,
                }}
              />
              <Box>
                <Typography variant="body2" fontWeight={500}>
                  Moderate (30-50)
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Increased caution
                </Typography>
              </Box>
            </Box>

            {/* Orange - Elevated */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  bgcolor: '#ff9800',
                  border: '2px solid #fff',
                  boxShadow: 1,
                }}
              />
              <Box>
                <Typography variant="body2" fontWeight={500}>
                  Elevated (50-70)
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Consider postponing
                </Typography>
              </Box>
            </Box>

            {/* Red - High Risk */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  bgcolor: '#f44336',
                  border: '2px solid #fff',
                  boxShadow: 1,
                }}
              />
              <Box>
                <Typography variant="body2" fontWeight={500}>
                  High Risk (70+)
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Not recommended
                </Typography>
              </Box>
            </Box>

            {/* Gray - No Data */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <Box
                sx={{
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  bgcolor: '#9e9e9e',
                  border: '2px solid #fff',
                  boxShadow: 1,
                }}
              />
              <Box>
                <Typography variant="body2" fontWeight={500}>
                  No Data
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Insufficient information
                </Typography>
              </Box>
            </Box>
          </Box>

          <Divider sx={{ my: 1.5 }} />

          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
            💡 <strong>Heatmap:</strong> Regional risk coverage across entire map
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            📍 <strong>Markers:</strong> Individual routes • Clusters show average score
          </Typography>
        </Paper>

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
        routeData={selectedRoute && safetyData ? {
          route_id: selectedRoute.properties.id,
          name: selectedRoute.properties.name,
          mountain_name: selectedRoute.properties.mountain_name || 'Unknown Mountain',
          type: selectedRoute.properties.type,
          grade: selectedRoute.properties.grade,
          latitude: selectedRoute.geometry.coordinates[1],
          longitude: selectedRoute.geometry.coordinates[0],
          elevation_meters: null,
          risk_score: safetyData.risk_score || 0,
          color_code: safetyData.color_code || 'gray',
          mp_route_id: selectedRoute.properties.mp_route_id,
        } : null}
        selectedDate={format(selectedDate, 'yyyy-MM-dd')}
      />
    </LocalizationProvider>
  );
}

/**
 * Get background color for safety score display
 * @deprecated Kept for potential future use
 */
function _getSafetyBackgroundColor(colorCode) {
  const colors = {
    green: '#4caf50',
    yellow: '#ffeb3b',
    orange: '#ff9800',
    red: '#f44336',
    gray: '#9e9e9e',
  };
  return colors[colorCode] || colors.gray;
}

/**
 * Get human-readable safety interpretation
 * @deprecated Kept for potential future use
 */
function _getSafetyInterpretation(riskScore) {
  if (riskScore < 30) {
    return '✅ Conditions appear favorable for climbing. Standard precautions apply.';
  } else if (riskScore < 50) {
    return '⚠️ Moderate risk conditions. Exercise increased caution and proper preparation.';
  } else if (riskScore < 70) {
    return '🔶 Elevated risk conditions. Consider postponing or choosing alternative routes.';
  } else {
    return '🔴 High risk conditions. Climbing not recommended unless experienced with current conditions.';
  }
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
