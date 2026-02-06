/**
 * PredictionForm Component - Material Design
 *
 * Form for searching routes and selecting prediction dates.
 */
import { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  Typography,
  TextField,
  Button,
  Box,
  CircularProgress,
  InputAdornment,
  Autocomplete,
} from '@mui/material';
import {
  Search as SearchIcon,
  CalendarToday as CalendarIcon,
} from '@mui/icons-material';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

/**
 * Get today's date in YYYY-MM-DD format
 */
const getTodayDate = () => {
  return new Date().toISOString().split('T')[0];
};

/**
 * Get max date (7 days from now) in YYYY-MM-DD format
 */
const getMaxDate = () => {
  const date = new Date();
  date.setDate(date.getDate() + 7);
  return date.toISOString().split('T')[0];
};

/**
 * PredictionForm - Route search and date selection
 *
 * @param {Function} onSubmit - Callback when form is submitted
 * @param {boolean} isLoading - Whether prediction is loading
 * @param {Function} onRouteSelect - Callback when route is selected from search
 */
export default function PredictionForm({ onSubmit, isLoading, onRouteSelect }) {
  const [routeSearch, setRouteSearch] = useState('');
  const [plannedDate, setPlannedDate] = useState(getTodayDate());
  const [routeOptions, setRouteOptions] = useState([]);
  const [loadingRoutes, setLoadingRoutes] = useState(false);
  const [selectedRoute, setSelectedRoute] = useState(null);

  // Fetch route and mountain suggestions when user types
  useEffect(() => {
    if (!routeSearch || routeSearch.length < 2) {
      setRouteOptions([]);
      return;
    }

    const fetchOptions = async () => {
      setLoadingRoutes(true);
      try {
        // Fetch both MP routes and locations in parallel
        const [routesResponse, locationsResponse] = await Promise.all([
          fetch(`${API_BASE_URL}/mp-routes?search=${encodeURIComponent(routeSearch)}&limit=8`),
          fetch(`${API_BASE_URL}/locations?search=${encodeURIComponent(routeSearch)}&limit=5`)
        ]);

        const routesData = await routesResponse.json();
        const locationsData = await locationsResponse.json();

        // Combine results - locations first (marked as resultType: 'location'), then routes
        const locations = (locationsData.data || []).map(loc => ({
          ...loc,
          resultType: 'location',  // Use resultType to distinguish result type
          route_count: loc.route_count || 0,
          // Map mp_id to mountain_id for backwards compatibility
          mountain_id: loc.mp_id
        }));

        const routes = (routesData.data || []).map(r => ({
          ...r,
          resultType: 'route',  // Use resultType to distinguish from route's actual climbing type
          routeType: r.type,    // Preserve the actual route type (Ice, Trad, Sport, etc.)
          // Map mp_route_id to route_id for backwards compatibility
          route_id: r.mp_route_id
        }));

        setRouteOptions([...locations, ...routes]);
      } catch (err) {
        console.error('Error fetching options:', err);
        setRouteOptions([]);
      } finally {
        setLoadingRoutes(false);
      }
    };

    // Debounce the search
    const timeout = setTimeout(fetchOptions, 300);
    return () => clearTimeout(timeout);
  }, [routeSearch]);

  const handleRouteSelect = (event, value) => {
    setSelectedRoute(value);
    if (value && onRouteSelect) {
      // Pass the selected item (route or mountain) to parent
      onRouteSelect(value);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();

    if (!selectedRoute) {
      alert('Please select a route or mountain from the search results');
      return;
    }

    // If location is selected, don't submit prediction - just zoom to it
    if (selectedRoute.resultType === 'location') {
      alert('Location selected. Click on a route marker to get its safety prediction.');
      return;
    }

    onSubmit({
      route_id: selectedRoute.route_id,
      route_name: selectedRoute.name,
      planned_date: plannedDate,
    });
  };

  return (
    <Card elevation={3}>
      <CardContent>
        <Typography variant="h5" component="h2" gutterBottom fontWeight={500}>
          Route Search
        </Typography>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          Search for a route or click on a route marker on the map to get safety predictions.
        </Typography>

        <Box component="form" onSubmit={handleSubmit}>
          {/* Route Search */}
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle1" fontWeight={500} gutterBottom>
              Route Name
            </Typography>
            <Autocomplete
              value={selectedRoute}
              onChange={handleRouteSelect}
              inputValue={routeSearch}
              onInputChange={(event, newValue) => setRouteSearch(newValue)}
              options={routeOptions}
              getOptionLabel={(option) => option.name || ''}
              loading={loadingRoutes}
              disabled={isLoading}
              groupBy={(option) => option.resultType === 'location' ? 'Locations' : 'Routes'}
              renderInput={(params) => (
                <TextField
                  {...params}
                  placeholder="e.g., Moby Grape, El Capitan"
                  required
                  InputProps={{
                    ...params.InputProps,
                    startAdornment: (
                      <>
                        <InputAdornment position="start">
                          <SearchIcon color="action" />
                        </InputAdornment>
                        {params.InputProps.startAdornment}
                      </>
                    ),
                    endAdornment: (
                      <>
                        {loadingRoutes ? <CircularProgress color="inherit" size={20} /> : null}
                        {params.InputProps.endAdornment}
                      </>
                    ),
                  }}
                />
              )}
              renderOption={(props, option) => (
                <Box component="li" {...props}>
                  <Box sx={{ flexGrow: 1 }}>
                    <Typography
                      variant="body2"
                      sx={{ fontWeight: option.resultType === 'location' ? 600 : 400 }}
                    >
                      {option.name}
                      {option.resultType === 'location' && option.route_count > 0 && (
                        <Typography component="span" variant="caption" sx={{ ml: 1, color: 'primary.main' }}>
                          ({option.route_count} routes)
                        </Typography>
                      )}
                    </Typography>
                    {option.resultType === 'route' && (
                      <Typography variant="caption" color="text.secondary">
                        {option.grade || 'N/A'} • {option.routeType || 'Unknown type'}
                      </Typography>
                    )}
                    {option.resultType === 'location' && (
                      <Typography variant="caption" color="text.secondary">
                        {option.latitude && option.longitude
                          ? `${option.latitude.toFixed(2)}°, ${option.longitude.toFixed(2)}°`
                          : 'Coordinates unknown'}
                      </Typography>
                    )}
                  </Box>
                </Box>
              )}
              noOptionsText={routeSearch.length < 2 ? 'Type to search routes or locations...' : 'No results found'}
            />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
              Search by route name or climbing area (case-insensitive) • Click route markers for details
            </Typography>
          </Box>

          {/* Planned Date */}
          <Box sx={{ mb: 3 }}>
            <Typography variant="subtitle1" fontWeight={500} gutterBottom>
              Planned Date
            </Typography>
            <TextField
              type="date"
              value={plannedDate}
              onChange={(e) => setPlannedDate(e.target.value)}
              fullWidth
              required
              disabled={isLoading}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <CalendarIcon color="action" />
                  </InputAdornment>
                ),
              }}
              inputProps={{
                min: getTodayDate(),
                max: getMaxDate(),
              }}
            />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
              Weather forecasts available for the next 7 days
            </Typography>
          </Box>

          {/* Submit Button */}
          <Button
            type="submit"
            variant="contained"
            color="primary"
            size="large"
            fullWidth
            disabled={isLoading}
            startIcon={isLoading ? <CircularProgress size={20} color="inherit" /> : <SearchIcon />}
            sx={{
              py: 1.5,
              fontWeight: 600,
              fontSize: '1rem',
            }}
          >
            {isLoading ? 'Analyzing Route...' : 'Search'}
          </Button>
        </Box>

        {/* Help Text */}
        <Box sx={{ mt: 3, p: 2, bgcolor: 'info.50', borderRadius: 1, border: 1, borderColor: 'info.200' }}>
          <Typography variant="body2" color="text.secondary">
            <strong>💡 Tip:</strong> Route data includes elevation and route type automatically.
            Click route markers on the map to quickly select popular routes.
          </Typography>
        </Box>
      </CardContent>
    </Card>
  );
}
