import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Tabs,
  Tab,
  Typography,
  IconButton,
  CircularProgress,
  Card,
  CardContent,
  Grid,
  Chip,
  Divider,
  Alert,
  List,
  ListItem,
  ListItemText,
  Paper,
  Button,
  Menu,
  MenuItem,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import DownloadIcon from '@mui/icons-material/Download';
import LinearProgress from '@mui/material/LinearProgress';
import { keyframes } from '@mui/system';

// Custom shimmer animation for loading states
const shimmer = keyframes`
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
`;

/**
 * Get themed icon and color for route type
 * Returns emoji + label + color theme for consistent route type display
 */
function getRouteTypeTheme(type) {
  const normalizedType = (type || '').toLowerCase().trim();

  const themes = {
    'rock': { icon: '🪨', label: 'Rock', color: '#8B7355', bgColor: '#F5F0E8' },
    'trad': { icon: '🪨', label: 'Trad', color: '#8B7355', bgColor: '#F5F0E8' },
    'sport': { icon: '⚡', label: 'Sport', color: '#FF6B35', bgColor: '#FFF3EE' },
    'alpine': { icon: '🏔️', label: 'Alpine', color: '#4A90A4', bgColor: '#E8F4F8' },
    'ice': { icon: '🧊', label: 'Ice', color: '#00BCD4', bgColor: '#E0F7FA' },
    'mixed': { icon: '🧊🪨', label: 'Mixed', color: '#7B68EE', bgColor: '#F0EBFF' },
    'aid': { icon: '🔩', label: 'Aid', color: '#607D8B', bgColor: '#ECEFF1' },
    'toprope': { icon: '🧗', label: 'Top Rope', color: '#4CAF50', bgColor: '#E8F5E9' },
    'boulder': { icon: '💪', label: 'Boulder', color: '#FF9800', bgColor: '#FFF8E1' },
  };

  // Check for ice/mixed combinations
  if (normalizedType.includes('ice') && normalizedType.includes('mixed')) {
    return themes['mixed'];
  }
  if (normalizedType.includes('ice')) {
    return themes['ice'];
  }
  if (normalizedType.includes('mixed')) {
    return themes['mixed'];
  }

  // Direct matches
  if (themes[normalizedType]) {
    return themes[normalizedType];
  }

  // Default for unknown types
  return { icon: '🧗', label: type || 'Unknown', color: '#9E9E9E', bgColor: '#F5F5F5' };
}

// Loading component with Material Design 3 styling
function LoadingState({ message = 'Loading data...' }) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: 400,
        gap: 3,
      }}
    >
      <Box
        sx={{
          width: 80,
          height: 80,
          borderRadius: '50%',
          background: 'linear-gradient(135deg, #1976d2 0%, #42a5f5 50%, #1976d2 100%)',
          backgroundSize: '200% 200%',
          animation: `${shimmer} 2s ease-in-out infinite`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: '0 4px 20px rgba(25, 118, 210, 0.3)',
        }}
      >
        <CircularProgress
          size={48}
          thickness={2}
          sx={{
            color: 'white',
            '& .MuiCircularProgress-circle': {
              strokeLinecap: 'round',
            },
          }}
        />
      </Box>
      <Box sx={{ textAlign: 'center' }}>
        <Typography variant="h6" fontWeight={500} color="primary.main">
          {message}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          Analyzing route conditions...
        </Typography>
      </Box>
      <LinearProgress
        sx={{
          width: 200,
          height: 6,
          borderRadius: 3,
          bgcolor: 'grey.200',
          '& .MuiLinearProgress-bar': {
            borderRadius: 3,
            background: 'linear-gradient(90deg, #1976d2, #42a5f5, #1976d2)',
            backgroundSize: '200% 100%',
            animation: `${shimmer} 1.5s ease-in-out infinite`,
          },
        }}
      />
    </Box>
  );
}

// Fun "Data on its way" component for routes without data yet
const roadblockBounce = keyframes`
  0%, 100% { transform: translateY(0) rotate(-5deg); }
  50% { transform: translateY(-8px) rotate(5deg); }
`;

const progressPulse = keyframes`
  0% { width: 15%; left: 0; }
  50% { width: 35%; left: 30%; }
  100% { width: 15%; left: 85%; }
`;

function DataOnTheWay({ title = "Data on its way!", message }) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: 350,
        textAlign: 'center',
        py: 4,
      }}
    >
      {/* Animated roadblock emojis */}
      <Box sx={{ display: 'flex', gap: 2, mb: 3 }}>
        <Typography
          variant="h2"
          sx={{
            animation: `${roadblockBounce} 1.2s ease-in-out infinite`,
            animationDelay: '0s',
          }}
        >
          🚧
        </Typography>
        <Typography
          variant="h2"
          sx={{
            animation: `${roadblockBounce} 1.2s ease-in-out infinite`,
            animationDelay: '0.2s',
          }}
        >
          🏔️
        </Typography>
        <Typography
          variant="h2"
          sx={{
            animation: `${roadblockBounce} 1.2s ease-in-out infinite`,
            animationDelay: '0.4s',
          }}
        >
          🚧
        </Typography>
      </Box>

      {/* Title */}
      <Typography
        variant="h5"
        fontWeight={600}
        color="primary.main"
        gutterBottom
      >
        {title}
      </Typography>

      {/* Custom message */}
      {message && (
        <Typography
          variant="body1"
          color="text.secondary"
          sx={{ maxWidth: 400, mb: 3 }}
        >
          {message}
        </Typography>
      )}

      {/* Kinetic progress bar */}
      <Box
        sx={{
          width: 280,
          height: 8,
          bgcolor: 'grey.200',
          borderRadius: 4,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        <Box
          sx={{
            position: 'absolute',
            height: '100%',
            background: 'linear-gradient(90deg, #ff9800, #ffc107, #ff9800)',
            borderRadius: 4,
            animation: `${progressPulse} 2s ease-in-out infinite`,
          }}
        />
      </Box>

      {/* Check back later */}
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ mt: 2, fontStyle: 'italic' }}
      >
        🕐 Check back later for updates!
      </Typography>
    </Box>
  );
}

import {
  LineChart,
  Line,
  BarChart,
  Bar,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import { format } from 'date-fns';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

// Tab panel component
function TabPanel({ children, value, index, ...other }) {
  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`analytics-tabpanel-${index}`}
      aria-labelledby={`analytics-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ py: 3 }}>{children}</Box>}
    </div>
  );
}

export default function RouteAnalyticsModal({ open, onClose, routeData, selectedDate }) {
  const [currentTab, setCurrentTab] = useState(1);  // Default to Route Details tab
  const [loading, setLoading] = useState({});
  const [data, setData] = useState({
    forecast: null,
    routeDetails: null,
    accidents: null,
    breakdown: null,
    historical: null,
    timeOfDay: null,
    ascents: null,
  });
  const [error, setError] = useState(null);
  const [exportMenuAnchor, setExportMenuAnchor] = useState(null);

  // Reset tab when modal opens - default to Route Details (tab 1)
  useEffect(() => {
    if (open) {
      setCurrentTab(1);  // Route Details as default tab
      setError(null);
    }
  }, [open]);

  // Reset all cached data when route changes
  useEffect(() => {
    if (routeData?.route_id) {
      setData({
        forecast: null,
        routeDetails: null,
        accidents: null,
        breakdown: null,
        historical: null,
        timeOfDay: null,
        ascents: null,
      });
      setError(null);
    }
  }, [routeData?.route_id]);

  // Fetch data based on current tab
  useEffect(() => {
    if (!open || !routeData) return;

    const fetchTabData = async () => {
      try {
        switch (currentTab) {
          case 0: // 7-Day Forecast
            if (!data.forecast) {
              setLoading(prev => ({ ...prev, forecast: true }));
              const response = await fetch(
                `${API_BASE}/mp-routes/${routeData.route_id}/forecast?start_date=${selectedDate}`
              );
              if (!response.ok) throw new Error('Failed to fetch forecast data');
              const forecastData = await response.json();
              setData(prev => ({ ...prev, forecast: forecastData }));
              setLoading(prev => ({ ...prev, forecast: false }));
            }
            break;

          case 1: // Route Characteristics
            if (!data.routeDetails) {
              setLoading(prev => ({ ...prev, routeDetails: true }));
              const response = await fetch(`${API_BASE}/mp-routes/${routeData.route_id}`);
              if (!response.ok) throw new Error('Failed to fetch route details');
              const routeDetails = await response.json();
              setData(prev => ({ ...prev, routeDetails }));
              setLoading(prev => ({ ...prev, routeDetails: false }));
            }
            break;

          case 2: // Accident Reports
            if (!data.accidents) {
              setLoading(prev => ({ ...prev, accidents: true }));
              const response = await fetch(
                `${API_BASE}/mp-routes/${routeData.route_id}/accidents`
              );
              if (!response.ok) throw new Error('Failed to fetch accident data');
              const accidentData = await response.json();
              setData(prev => ({ ...prev, accidents: accidentData }));
              setLoading(prev => ({ ...prev, accidents: false }));
            }
            break;

          case 3: // Risk Score Breakdown
            if (!data.breakdown) {
              setLoading(prev => ({ ...prev, breakdown: true }));
              const response = await fetch(
                `${API_BASE}/mp-routes/${routeData.route_id}/risk-breakdown?target_date=${selectedDate}`
              );
              if (!response.ok) throw new Error('Failed to fetch risk breakdown');
              const breakdownData = await response.json();
              setData(prev => ({ ...prev, breakdown: breakdownData }));
              setLoading(prev => ({ ...prev, breakdown: false }));
            }
            break;

          case 4: // Risk Trends (Historical)
            if (!data.historical) {
              setLoading(prev => ({ ...prev, historical: true }));
              const response = await fetch(
                `${API_BASE}/mp-routes/${routeData.route_id}/historical-trends?days=365`
              );
              if (!response.ok) throw new Error('Failed to fetch historical data');
              const historicalData = await response.json();
              setData(prev => ({ ...prev, historical: historicalData }));
              setLoading(prev => ({ ...prev, historical: false }));
            }
            break;

          case 5: // Time of Day Analysis
            if (!data.timeOfDay) {
              setLoading(prev => ({ ...prev, timeOfDay: true }));
              const response = await fetch(
                `${API_BASE}/mp-routes/${routeData.route_id}/time-of-day?target_date=${selectedDate}`
              );
              if (!response.ok) throw new Error('Failed to fetch time-of-day data');
              const timeOfDayData = await response.json();
              setData(prev => ({ ...prev, timeOfDay: timeOfDayData }));
              setLoading(prev => ({ ...prev, timeOfDay: false }));
            }
            break;

          case 6: // Ascent Analytics
            if (!data.ascents) {
              setLoading(prev => ({ ...prev, ascents: true }));
              const response = await fetch(
                `${API_BASE}/mp-routes/${routeData.route_id}/ascent-analytics`
              );
              if (!response.ok) throw new Error('Failed to fetch ascent analytics');
              const ascentsData = await response.json();
              setData(prev => ({ ...prev, ascents: ascentsData }));
              setLoading(prev => ({ ...prev, ascents: false }));
            }
            break;

          default:
            break;
        }
      } catch (err) {
        console.error('Error fetching tab data:', err);
        setError(err.message);
        // Clear loading state for the current tab
        const tabKeys = ['forecast', 'routeDetails', 'accidents', 'breakdown', 'historical', 'timeOfDay', 'ascents'];
        const currentTabKey = tabKeys[currentTab];
        if (currentTabKey) {
          setLoading(prev => ({ ...prev, [currentTabKey]: false }));
        }
      }
    };

    fetchTabData();
  }, [currentTab, open, routeData, selectedDate]);

  const handleTabChange = (event, newValue) => {
    setCurrentTab(newValue);
    setError(null);
  };

  // Export functions
  const handleExportClick = (event) => {
    setExportMenuAnchor(event.currentTarget);
  };

  const handleExportClose = () => {
    setExportMenuAnchor(null);
  };

  const exportAsJSON = () => {
    const exportData = {
      route: routeData,
      date: selectedDate,
      analytics: data,
      exported_at: new Date().toISOString(),
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${routeData.name.replace(/[^a-z0-9]/gi, '_')}_analytics_${selectedDate}.json`;
    link.click();
    URL.revokeObjectURL(url);
    handleExportClose();
  };

  const exportAsCSV = () => {
    // Build CSV with key metrics
    let csv = 'SafeAscent Route Analytics Export\n\n';
    csv += `Route Name,${routeData.name}\n`;
    csv += `Location,${data.routeDetails?.location_name || routeData.mountain_name || 'Unknown'}\n`;
    csv += `Type,${routeData.type}\n`;
    csv += `Grade,${routeData.grade}\n`;
    csv += `Risk Score,${routeData.risk_score}\n`;
    csv += `Date,${selectedDate}\n\n`;

    // Add 7-day forecast if available
    if (data.forecast && data.forecast.forecast_days) {
      csv += '\n7-Day Forecast\n';
      csv += 'Date,Risk Score,Weather Summary,Temp High,Temp Low,Precip,Wind Speed\n';
      data.forecast.forecast_days.forEach(day => {
        csv += `${day.date},${day.risk_score},"${day.weather_summary}",${day.temp_high},${day.temp_low},${day.precip_mm || 0},${day.wind_speed}\n`;
      });
    }

    // Add accident reports if available
    if (data.accidents && data.accidents.accidents) {
      csv += '\nAccident Reports\n';
      csv += 'Date,Route,Same Route,Severity,Description\n';
      data.accidents.accidents.slice(0, 20).forEach(acc => {
        const desc = (acc.description || '').replace(/"/g, '""').substring(0, 100);
        csv += `${acc.date || 'Unknown'},${acc.route_name},${acc.same_route ? 'Yes' : 'No'},${acc.injury_severity || 'Unknown'},"${desc}"\n`;
      });
    }

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${routeData.name.replace(/[^a-z0-9]/gi, '_')}_analytics_${selectedDate}.csv`;
    link.click();
    URL.revokeObjectURL(url);
    handleExportClose();
  };

  if (!routeData) return null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xl"
      fullWidth
      PaperProps={{
        sx: {
          height: '90vh',
          maxHeight: '90vh',
        },
      }}
    >
      <DialogTitle sx={{ bgcolor: 'primary.main', color: 'white', pr: 14 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', pr: 8 }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h5" component="div" fontWeight={600}>
              {routeData.name}
            </Typography>
            <Typography variant="body2" sx={{ opacity: 0.9, mt: 0.5 }}>
              {data.routeDetails?.location_name || routeData.mountain_name || 'Unknown'} • {routeData.type} • Grade {routeData.grade}
            </Typography>
          </Box>
          <Chip
            label={`Risk: ${routeData.risk_score}/100`}
            sx={{
              bgcolor: routeData.color_code === 'green' ? 'success.main' :
                       routeData.color_code === 'yellow' ? 'warning.main' :
                       routeData.color_code === 'orange' ? 'warning.dark' : 'error.main',
              color: 'white',
              fontWeight: 600,
              fontSize: '1rem',
              mr: 2,
            }}
          />
        </Box>
        <IconButton
          onClick={handleExportClick}
          sx={{
            position: 'absolute',
            right: 56,
            top: 12,
            color: 'white',
            '&:hover': { bgcolor: 'rgba(255,255,255,0.1)' },
          }}
          title="Export Analytics Data"
        >
          <DownloadIcon />
        </IconButton>
        <IconButton
          onClick={onClose}
          sx={{
            position: 'absolute',
            right: 12,
            top: 12,
            color: 'white',
            '&:hover': { bgcolor: 'rgba(255,255,255,0.1)' },
          }}
        >
          <CloseIcon />
        </IconButton>
        <Menu
          anchorEl={exportMenuAnchor}
          open={Boolean(exportMenuAnchor)}
          onClose={handleExportClose}
        >
          <MenuItem onClick={exportAsJSON}>
            <Typography variant="body2">📄 Export as JSON</Typography>
          </MenuItem>
          <MenuItem onClick={exportAsCSV}>
            <Typography variant="body2">📊 Export as CSV</Typography>
          </MenuItem>
        </Menu>
      </DialogTitle>

      <Box sx={{ borderBottom: 1, borderColor: 'divider', bgcolor: 'grey.50' }}>
        <Tabs
          value={currentTab}
          onChange={handleTabChange}
          variant="fullWidth"
          sx={{
            '& .MuiTab-root': {
              minHeight: 64,
              fontSize: '0.9rem',
              fontWeight: 500,
            },
          }}
        >
          <Tab label="7-Day Forecast" />
          <Tab label="Route Details" />
          <Tab label="Accident Reports" />
          <Tab label="Risk Breakdown" />
          <Tab label="Risk Trends" />
          <Tab label="Time of Day" />
          <Tab label="Ascents" />
        </Tabs>
      </Box>

      <DialogContent sx={{ bgcolor: 'grey.50', overflow: 'auto' }}>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}

        {/* Tab 0: 7-Day Forecast */}
        <TabPanel value={currentTab} index={0}>
          <ForecastTab
            data={data.forecast}
            loading={loading.forecast}
            selectedDate={selectedDate}
            routeData={routeData}
          />
        </TabPanel>

        {/* Tab 1: Route Characteristics */}
        <TabPanel value={currentTab} index={1}>
          <RouteDetailsTab
            data={data.routeDetails}
            loading={loading.routeDetails}
            routeData={routeData}
          />
        </TabPanel>

        {/* Tab 2: Accident Reports */}
        <TabPanel value={currentTab} index={2}>
          <AccidentsTab
            data={data.accidents}
            loading={loading.accidents}
            routeData={routeData}
          />
        </TabPanel>

        {/* Tab 3: Risk Score Breakdown */}
        <TabPanel value={currentTab} index={3}>
          <RiskBreakdownTab
            data={data.breakdown}
            loading={loading.breakdown}
            routeData={routeData}
          />
        </TabPanel>

        {/* Tab 4: Risk Trends (Historical) */}
        <TabPanel value={currentTab} index={4}>
          <RiskTrendsTab
            data={data.historical}
            loading={loading.historical}
            routeData={routeData}
          />
        </TabPanel>

        {/* Tab 5: Time of Day Analysis */}
        <TabPanel value={currentTab} index={5}>
          <TimeOfDayTab
            data={data.timeOfDay}
            loading={loading.timeOfDay}
            routeData={routeData}
            selectedDate={selectedDate}
          />
        </TabPanel>

        {/* Tab 6: Ascent Analytics */}
        <TabPanel value={currentTab} index={6}>
          <AscentsTab
            data={data.ascents}
            loading={loading.ascents}
            routeData={routeData}
          />
        </TabPanel>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================================
// TAB COMPONENTS
// ============================================================================

// Helper to convert Celsius to Fahrenheit
const celsiusToFahrenheit = (celsius) => {
  if (celsius === null || celsius === undefined) return null;
  return Math.round(celsius * 9/5 + 32);
};

// Helper to convert m/s to mph
const msToMph = (ms) => {
  if (ms === null || ms === undefined) return null;
  return Math.round(ms * 2.237);
};

function ForecastTab({ data, loading, selectedDate: _selectedDate, routeData }) {
  if (loading) {
    return <LoadingState message="Loading forecast data..." />;
  }

  if (!data) {
    return (
      <Alert severity="info">
        No forecast data available. Try selecting a different date.
      </Alert>
    );
  }

  // Get today's data from forecast
  const today = data.today || data.forecast_days?.[0] || {};

  // Convert temps from Celsius to Fahrenheit for display
  const tempHighF = celsiusToFahrenheit(today.temp_high);
  const tempLowF = celsiusToFahrenheit(today.temp_low);
  const windMph = msToMph(today.wind_speed);
  const precipChance = today.precip_chance ?? (today.precip_mm ? Math.min(Math.round(today.precip_mm * 10), 100) : null);

  return (
    <Grid container spacing={3}>
      {/* Today's Weather Highlight */}
      <Grid size={12}>
        <Card elevation={3}>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📍 Today's Conditions at Route Location
            </Typography>
            <Grid container spacing={2} sx={{ mt: 1 }}>
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="body2" color="text.secondary">Temperature</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {tempHighF !== null && tempLowF !== null
                    ? `${tempHighF}°F / ${tempLowF}°F`
                    : today.temp_high !== null && today.temp_low !== null
                      ? `${Math.round(today.temp_high)}°C / ${Math.round(today.temp_low)}°C`
                      : 'N/A'}
                </Typography>
              </Grid>
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="body2" color="text.secondary">Precipitation</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {precipChance !== null ? `${precipChance}%` : 'N/A'}
                </Typography>
              </Grid>
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="body2" color="text.secondary">Wind Speed</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {windMph !== null ? `${windMph} mph` : today.wind_speed !== null ? `${Math.round(today.wind_speed)} m/s` : 'N/A'}
                </Typography>
              </Grid>
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="body2" color="text.secondary">Elevation</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {routeData.elevation_meters ? `${Math.round(routeData.elevation_meters * 3.28084)} ft` : 'N/A'}
                </Typography>
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      </Grid>

      {/* 7-Day Risk Score Trend */}
      <Grid size={{ xs: 12, md: 8 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              7-Day Risk Score Forecast
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={data.forecast_days || []}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="date"
                  tickFormatter={(date) => format(new Date(date), 'EEE M/d')}
                />
                <YAxis domain={[0, 100]} />
                <Tooltip
                  labelFormatter={(date) => format(new Date(date), 'EEEE, MMM d')}
                  formatter={(value) => [`${value}/100`, 'Risk Score']}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="risk_score"
                  stroke="#1976d2"
                  strokeWidth={3}
                  name="Risk Score"
                  dot={{ r: 6 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </Grid>

      {/* 7-Day Weather Summary */}
      <Grid size={{ xs: 12, md: 4 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              Weather Summary
            </Typography>
            <List dense>
              {data.forecast_days?.slice(0, 7).map((day, idx) => (
                <React.Fragment key={idx}>
                  <ListItem>
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <Typography variant="body1" fontWeight={500}>
                            {format(new Date(day.date), 'EEE M/d')}
                          </Typography>
                          <Chip
                            size="small"
                            label={`${day.risk_score}`}
                            sx={{
                              bgcolor: day.risk_score < 35 ? 'success.main' :
                                       day.risk_score < 55 ? 'warning.main' :
                                       day.risk_score < 75 ? 'warning.dark' : 'error.main',
                              color: 'white',
                              fontWeight: 600,
                            }}
                          />
                        </Box>
                      }
                      secondary={
                        <Typography variant="caption" component="div">
                          {day.weather_summary || 'No data'}
                        </Typography>
                      }
                    />
                  </ListItem>
                  {idx < 6 && <Divider />}
                </React.Fragment>
              ))}
            </List>
          </CardContent>
        </Card>
      </Grid>
    </Grid>
  );
}

function RouteDetailsTab({ data, loading, routeData }) {
  if (loading) {
    return <LoadingState message="Loading route details..." />;
  }

  const details = data || routeData;
  const routeTheme = getRouteTypeTheme(details.type);

  // Format elevation display
  const elevationDisplay = details.elevation_meters
    ? `${Math.round(details.elevation_meters * 3.28084).toLocaleString()} ft (${Math.round(details.elevation_meters).toLocaleString()}m)`
    : null;

  // Format coordinates
  const coordsDisplay = details.latitude && details.longitude
    ? `${details.latitude.toFixed(4)}°, ${details.longitude.toFixed(4)}°`
    : null;

  return (
    <Card elevation={2}>
      <CardContent sx={{ p: 3 }}>
        {/* Route Header with Type Badge */}
        <Box sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', mb: 3 }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="h5" fontWeight={700} gutterBottom>
              {details.name}
            </Typography>
            <Typography variant="body1" color="text.secondary">
              📍 {details.location_name || details.mountain_name || 'Unknown Location'}
            </Typography>
          </Box>

          {/* Route Type Badge */}
          <Chip
            label={
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <span style={{ fontSize: '1.1rem' }}>{routeTheme.icon}</span>
                <span>{routeTheme.label}</span>
              </Box>
            }
            sx={{
              bgcolor: routeTheme.bgColor,
              color: routeTheme.color,
              fontWeight: 600,
              fontSize: '0.95rem',
              py: 2.5,
              px: 1,
              border: `1.5px solid ${routeTheme.color}20`,
            }}
          />
        </Box>

        <Divider sx={{ mb: 3 }} />

        {/* Route Details Grid */}
        <Grid container spacing={3}>
          {/* Grade */}
          <Grid size={{ xs: 6, sm: 3, md: 2.4 }}>
            <Box sx={{ textAlign: 'center', p: 2, bgcolor: 'primary.50', borderRadius: 2, border: '1px solid', borderColor: 'primary.100' }}>
              <Typography variant="h4" fontWeight={700} sx={{ color: '#1565c0' }}>
                {details.grade || '—'}
              </Typography>
              <Typography variant="body2" sx={{ color: '#424242', fontWeight: 500 }}>
                🎯 Grade
              </Typography>
            </Box>
          </Grid>

          {/* Pitches */}
          <Grid size={{ xs: 6, sm: 3, md: 2.4 }}>
            <Box sx={{ textAlign: 'center', p: 2, bgcolor: '#f5f5f5', borderRadius: 2, border: '1px solid #e0e0e0' }}>
              <Typography variant="h4" fontWeight={700} sx={{ color: '#212121' }}>
                {details.pitches ? Math.round(details.pitches) : 1}
              </Typography>
              <Typography variant="body2" sx={{ color: '#424242', fontWeight: 500 }}>
                📏 Pitches
              </Typography>
            </Box>
          </Grid>

          {/* Elevation */}
          <Grid size={{ xs: 6, sm: 3, md: 2.4 }}>
            <Box sx={{ textAlign: 'center', p: 2, bgcolor: '#e8f5e9', borderRadius: 2, border: '1px solid #c8e6c9' }}>
              <Typography variant="h4" fontWeight={700} sx={{ color: '#2e7d32' }}>
                {elevationDisplay ? elevationDisplay.split(' ')[0] : '—'}
              </Typography>
              <Typography variant="body2" sx={{ color: '#424242', fontWeight: 500 }}>
                ⛰️ Elevation
              </Typography>
            </Box>
          </Grid>

          {/* Coordinates */}
          <Grid size={{ xs: 6, sm: 3, md: 4.8 }}>
            <Box sx={{ textAlign: 'center', p: 2, bgcolor: '#fff3e0', borderRadius: 2, border: '1px solid #ffe0b2' }}>
              <Typography variant="h6" fontWeight={600} sx={{ color: '#e65100', fontFamily: 'monospace' }}>
                {coordsDisplay || '—'}
              </Typography>
              <Typography variant="body2" sx={{ color: '#424242', fontWeight: 500 }}>
                🧭 GPS Coordinates
              </Typography>
            </Box>
          </Grid>
        </Grid>

        {/* Mountain Project Link */}
        {details.url && (
          <Box sx={{ mt: 3, pt: 3, borderTop: '1px solid', borderColor: 'divider' }}>
            <Button
              component="a"
              href={details.url}
              target="_blank"
              rel="noopener noreferrer"
              variant="outlined"
              fullWidth
              sx={{
                py: 1.5,
                fontSize: '1rem',
                fontWeight: 600,
                borderRadius: 2,
                textTransform: 'none',
              }}
            >
              🏔️ View on Mountain Project ↗
            </Button>
          </Box>
        )}

        {/* Approach Notes (if available) */}
        {details.approach_notes && (
          <Box sx={{ mt: 3, pt: 3, borderTop: '1px solid', borderColor: 'divider' }}>
            <Typography variant="subtitle1" fontWeight={600} gutterBottom>
              🥾 Approach Notes
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.7 }}>
              {details.approach_notes}
            </Typography>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

function AccidentsTab({ data, loading, routeData }) {
  const [showCount, setShowCount] = useState(10);

  if (loading) {
    return <LoadingState message="Loading accident reports..." />;
  }

  if (!data || data.accidents?.length === 0) {
    return (
      <Alert severity="info">
        No accident reports found for this mountain.
      </Alert>
    );
  }

  const displayedAccidents = data.accidents.slice(0, showCount);
  const hasMore = data.accidents.length > showCount;

  return (
    <Box>
      <Typography variant="h6" gutterBottom fontWeight={600}>
        ⚠️ Accident Reports for {data.location_name || routeData.mountain_name || routeData.name}
      </Typography>
      <Typography variant="body2" color="text.secondary" paragraph>
        Showing {displayedAccidents.length} of {data.accidents.length} accidents.
        Accidents on the same route are highlighted.
      </Typography>

      <Grid container spacing={2}>
        {displayedAccidents.map((accident, idx) => (
          <Grid size={12} key={idx}>
            <Card
              elevation={accident.same_route ? 4 : 1}
              sx={{
                border: accident.same_route ? 2 : 1,
                borderColor: accident.same_route ? 'error.main' : 'divider',
                bgcolor: accident.same_route ? 'error.50' : 'background.paper',
              }}
            >
              <CardContent>
                {/* Header Row: Date, Severity Badge, Relevance */}
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
                  <Box sx={{ flex: 1 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1 }}>
                      <Typography variant="subtitle1" fontWeight={700}>
                        📅 {accident.date ? format(new Date(accident.date), 'MMMM d, yyyy') : 'Date unknown'}
                      </Typography>
                      {accident.same_route && (
                        <Chip label="SAME ROUTE" size="small" color="error" sx={{ fontWeight: 600 }} />
                      )}
                      {accident.injury_severity && (
                        <Chip
                          label={accident.injury_severity}
                          size="small"
                          sx={{
                            fontWeight: 600,
                            bgcolor: accident.injury_severity.toLowerCase().includes('fatal') || accident.injury_severity.toLowerCase().includes('death') ? '#b71c1c' :
                                     accident.injury_severity.toLowerCase().includes('serious') || accident.injury_severity.toLowerCase().includes('severe') ? '#e65100' :
                                     accident.injury_severity.toLowerCase().includes('moderate') ? '#f57c00' :
                                     accident.injury_severity.toLowerCase().includes('minor') ? '#fbc02d' : '#9e9e9e',
                            color: 'white',
                          }}
                        />
                      )}
                    </Box>
                  </Box>

                  {/* Impact bar visualization with distance */}
                  <Box sx={{ width: 180, textAlign: 'right', flexShrink: 0 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
                      Relevance: {accident.impact_score ? `${Math.round(accident.impact_score)}%` : '—'}
                    </Typography>
                    <Box sx={{ height: 6, bgcolor: 'grey.200', borderRadius: 1, overflow: 'hidden', mt: 0.5 }}>
                      <Box
                        sx={{
                          height: '100%',
                          width: `${accident.impact_score || 10}%`,
                          bgcolor: accident.same_route ? 'error.main' : accident.impact_score > 50 ? 'warning.main' : 'info.main',
                        }}
                      />
                    </Box>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                      {accident.distance_km !== undefined ? `${accident.distance_km.toFixed(1)} km away` : ''}
                    </Typography>
                  </Box>
                </Box>

                {/* Location & Route Info Grid */}
                <Paper sx={{ p: 1.5, bgcolor: 'grey.50', mb: 2, borderRadius: 1 }}>
                  <Grid container spacing={1}>
                    <Grid size={{ xs: 6, sm: 3 }}>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, fontSize: '0.65rem' }}>
                        📍 LOCATION
                      </Typography>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {accident.mountain || accident.location || accident.state || 'Unknown'}
                      </Typography>
                    </Grid>
                    <Grid size={{ xs: 6, sm: 3 }}>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, fontSize: '0.65rem' }}>
                        🧗 ROUTE
                      </Typography>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {accident.route_name || 'Unknown'}
                      </Typography>
                    </Grid>
                    <Grid size={{ xs: 6, sm: 3 }}>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, fontSize: '0.65rem' }}>
                        ⚡ ACTIVITY
                      </Typography>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {accident.activity || accident.accident_type || 'Unknown'}
                      </Typography>
                    </Grid>
                    <Grid size={{ xs: 6, sm: 3 }}>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, fontSize: '0.65rem' }}>
                        📰 SOURCE
                      </Typography>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {accident.source || 'Unknown'}
                      </Typography>
                    </Grid>
                  </Grid>
                  {/* Second row for additional details */}
                  {(accident.state || accident.elevation_meters || accident.age_range) && (
                    <Grid container spacing={1} sx={{ mt: 1, pt: 1, borderTop: '1px solid', borderColor: 'divider' }}>
                      {accident.state && (
                        <Grid size={{ xs: 6, sm: 3 }}>
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, fontSize: '0.65rem' }}>
                            🏛️ STATE
                          </Typography>
                          <Typography variant="body2" sx={{ fontWeight: 500 }}>{accident.state}</Typography>
                        </Grid>
                      )}
                      {accident.elevation_meters && (
                        <Grid size={{ xs: 6, sm: 3 }}>
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, fontSize: '0.65rem' }}>
                            ⛰️ ELEVATION
                          </Typography>
                          <Typography variant="body2" sx={{ fontWeight: 500 }}>
                            {Math.round(accident.elevation_meters * 3.28084).toLocaleString()} ft
                          </Typography>
                        </Grid>
                      )}
                      {accident.age_range && (
                        <Grid size={{ xs: 6, sm: 3 }}>
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, fontSize: '0.65rem' }}>
                            👤 AGE
                          </Typography>
                          <Typography variant="body2" sx={{ fontWeight: 500 }}>{accident.age_range}</Typography>
                        </Grid>
                      )}
                      {accident.accident_type && accident.activity && accident.accident_type !== accident.activity && (
                        <Grid size={{ xs: 6, sm: 3 }}>
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, fontSize: '0.65rem' }}>
                            ⚠️ TYPE
                          </Typography>
                          <Typography variant="body2" sx={{ fontWeight: 500 }}>{accident.accident_type}</Typography>
                        </Grid>
                      )}
                    </Grid>
                  )}
                </Paper>

                {/* Description */}
                {accident.description && (
                  <Box sx={{ mb: 2 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, mb: 0.5 }}>
                      📝 DESCRIPTION
                    </Typography>
                    <Typography variant="body2" sx={{ lineHeight: 1.6, color: 'text.primary' }}>
                      {accident.description.length > 500 ? `${accident.description.substring(0, 500)}...` : accident.description}
                    </Typography>
                  </Box>
                )}

                {/* Tags if available */}
                {accident.tags && (
                  <Box sx={{ mb: 2 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, mb: 0.5 }}>
                      🏷️ TAGS
                    </Typography>
                    <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                      {accident.tags.split(',').map((tag, i) => (
                        <Chip key={i} label={tag.trim()} size="small" variant="outlined" sx={{ fontSize: '0.7rem' }} />
                      ))}
                    </Box>
                  </Box>
                )}

                {/* Weather Conditions */}
                {accident.weather && (
                  <Paper sx={{ p: 1.5, bgcolor: 'info.50', border: '1px solid', borderColor: 'info.200', borderRadius: 1 }}>
                    <Typography variant="caption" fontWeight={600} color="info.dark" sx={{ display: 'block', mb: 1 }}>
                      🌤️ WEATHER CONDITIONS ON ACCIDENT DATE
                    </Typography>
                    <Grid container spacing={2}>
                      <Grid size={3}>
                        <Typography variant="caption" color="text.secondary">Temperature</Typography>
                        <Typography variant="body2" fontWeight={600}>{accident.weather.temp || 'N/A'}</Typography>
                      </Grid>
                      <Grid size={3}>
                        <Typography variant="caption" color="text.secondary">Wind</Typography>
                        <Typography variant="body2" fontWeight={600}>
                          {accident.weather.wind_speed ? `${accident.weather.wind_speed} m/s` : 'N/A'}
                        </Typography>
                      </Grid>
                      <Grid size={3}>
                        <Typography variant="caption" color="text.secondary">Precipitation</Typography>
                        <Typography variant="body2" fontWeight={600}>
                          {accident.weather.precipitation ? `${accident.weather.precipitation} mm` : 'N/A'}
                        </Typography>
                      </Grid>
                      <Grid size={3}>
                        <Typography variant="caption" color="text.secondary">Conditions</Typography>
                        <Typography variant="body2" fontWeight={600}>{accident.weather.conditions || 'Unknown'}</Typography>
                      </Grid>
                    </Grid>
                  </Paper>
                )}
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      {hasMore && (
        <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
          <Chip
            label={`See More (${data.accidents.length - showCount} remaining)`}
            onClick={() => setShowCount(prev => prev + 10)}
            clickable
            color="primary"
            sx={{ fontWeight: 600 }}
          />
        </Box>
      )}
    </Box>
  );
}

function RiskBreakdownTab({ data, loading, routeData }) {
  if (loading) {
    return <LoadingState message="Analyzing risk factors..." />;
  }

  if (!data) {
    return (
      <Alert severity="info">
        No risk breakdown data available.
      </Alert>
    );
  }

  // Prepare data for pie chart showing factor contributions
  const factorData = data.factors?.map(factor => ({
    name: factor.name,
    value: factor.contribution,
    description: factor.description,
  })) || [];

  const COLORS = ['#f44336', '#ff9800', '#ffc107', '#4caf50', '#2196f3'];

  return (
    <Grid container spacing={3}>
      <Grid size={12}>
        <Card elevation={3}>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📊 Risk Score: {routeData.risk_score}/100
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              This risk score is calculated using statistical analysis of historical accident data,
              weather patterns, and route characteristics. Below is a breakdown of factors that
              contributed to this score.
            </Typography>
          </CardContent>
        </Card>
      </Grid>

      <Grid size={{ xs: 12, md: 6 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              Factor Contributions
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={factorData}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
                  outerRadius={100}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {factorData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </Grid>

      <Grid size={{ xs: 12, md: 6 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              Factor Details
            </Typography>
            <List>
              {data.factors?.map((factor, idx) => (
                <React.Fragment key={idx}>
                  <ListItem>
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <Typography variant="body1" fontWeight={600}>
                            {factor.name}
                          </Typography>
                          <Chip
                            label={`+${factor.contribution}`}
                            size="small"
                            color={factor.contribution > 20 ? 'error' : factor.contribution > 10 ? 'warning' : 'default'}
                          />
                        </Box>
                      }
                      secondary={factor.description}
                    />
                  </ListItem>
                  {idx < data.factors.length - 1 && <Divider />}
                </React.Fragment>
              ))}
            </List>
          </CardContent>
        </Card>
      </Grid>

      <Grid size={12}>
        <Alert severity="warning">
          <Typography variant="body2" fontWeight={600}>
            Statistical Analysis, Not Prediction
          </Typography>
          <Typography variant="body2">
            This risk score is derived from statistical patterns in historical data. It is NOT a
            machine learning prediction or forecast. Always use your own judgment, assess current
            conditions on-site, and make decisions based on your experience level.
          </Typography>
        </Alert>
      </Grid>
    </Grid>
  );
}

function RiskTrendsTab({ data, loading, routeData: _routeData }) {
  if (loading) {
    return <LoadingState message="Loading risk trends..." />;
  }

  const daysOfData = data?.historical_predictions?.length || 0;
  const MIN_DAYS_REQUIRED = 30;

  // Show "coming soon" if no data or less than 30 days
  if (!data || !data.historical_predictions || daysOfData < MIN_DAYS_REQUIRED) {
    return (
      <DataOnTheWay
        title="📈 Risk Trends Coming Soon!"
        message={daysOfData > 0
          ? `We're collecting historical data for this route. Currently tracking ${daysOfData} day${daysOfData === 1 ? '' : 's'} - need at least ${MIN_DAYS_REQUIRED} days for trend analysis. Check back soon!`
          : "We're building up historical trends for this route. Our prediction models improve as we collect more data over time. Check back in a few weeks!"
        }
      />
    );
  }

  return (
    <Grid container spacing={3}>
      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📈 Risk Score History
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              Historical risk scores based on actual weather conditions. Data is collected daily and stored for up to 1 year.
            </Typography>
            <ResponsiveContainer width="100%" height={350}>
              <AreaChart data={data.historical_predictions}>
                <defs>
                  <linearGradient id="colorRisk" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#2196f3" stopOpacity={0.8} />
                    <stop offset="95%" stopColor="#2196f3" stopOpacity={0.1} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="date"
                  tickFormatter={(date) => format(new Date(date), 'M/d')}
                />
                <YAxis domain={[0, 100]} />
                <Tooltip
                  labelFormatter={(date) => format(new Date(date), 'MMM d, yyyy')}
                  formatter={(value) => [`${value}/100`, 'Risk Score']}
                />
                <Area
                  type="monotone"
                  dataKey="risk_score"
                  stroke="#2196f3"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#colorRisk)"
                  name="Risk Score"
                />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </Grid>

      <Grid size={{ xs: 12, md: 6 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📊 Summary Statistics
            </Typography>
            <Grid container spacing={2} sx={{ mt: 1 }}>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Average Risk</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {data.summary?.avg_risk}/100
                </Typography>
              </Grid>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Peak Risk</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {data.summary?.max_risk}/100
                </Typography>
              </Grid>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Minimum Risk</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {data.summary?.min_risk}/100
                </Typography>
              </Grid>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Days Tracked</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {data.historical_predictions?.length || 0}
                </Typography>
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      </Grid>

      <Grid size={{ xs: 12, md: 6 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📉 Trend Analysis
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              {data.trend?.direction === 'increasing' &&
                '⚠️ Risk has been trending upward over the past 30 days.'}
              {data.trend?.direction === 'decreasing' &&
                '✅ Risk has been trending downward over the past 30 days.'}
              {data.trend?.direction === 'stable' &&
                '➡️ Risk has remained relatively stable over the past 30 days.'}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {data.trend?.description || 'Trend analysis not available.'}
            </Typography>
          </CardContent>
        </Card>
      </Grid>
    </Grid>
  );
}

function TimeOfDayTab({ data, loading, routeData: _routeData, selectedDate }) {
  if (loading) {
    return <LoadingState message="Analyzing hourly conditions..." />;
  }

  // Handle error responses from API
  if (data?.error) {
    return (
      <Alert severity="warning">
        <Typography variant="body2" fontWeight={600}>Unable to load hourly analysis</Typography>
        <Typography variant="body2">{data.error}</Typography>
      </Alert>
    );
  }

  if (!data || !data.hourly_data) {
    return (
      <Alert severity="info">
        <Typography variant="body2" fontWeight={600}>Time-of-day analysis not available</Typography>
        <Typography variant="body2">
          Hourly weather data is only available for dates within the next 7 days.
          Selected date: {selectedDate}
        </Typography>
      </Alert>
    );
  }

  return (
    <Grid container spacing={3}>
      <Grid size={12}>
        <Card elevation={3}>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              ⏰ Hourly Risk Score Analysis for {format(new Date(selectedDate), 'MMM d, yyyy')}
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              Find the optimal climbing window by analyzing how conditions vary throughout the day.
            </Typography>
          </CardContent>
        </Card>
      </Grid>

      {/* Best Climbing Window */}
      {data.best_window && (
        <Grid size={12}>
          <Card elevation={4} sx={{ bgcolor: 'success.50', borderLeft: 4, borderColor: 'success.main' }}>
            <CardContent>
              <Typography variant="h6" gutterBottom fontWeight={600} color="success.dark">
                ✅ Best Climbing Window
              </Typography>
              <Grid container spacing={2} sx={{ mt: 1 }}>
                <Grid size={{ xs: 12, md: 3 }}>
                  <Typography variant="body2" color="text.secondary">Time Window</Typography>
                  <Typography variant="h5" fontWeight={600}>
                    {String(data.best_window.start_hour).padStart(2, '0')}:00 - {String(data.best_window.end_hour).padStart(2, '0')}:00
                  </Typography>
                </Grid>
                <Grid size={{ xs: 12, md: 3 }}>
                  <Typography variant="body2" color="text.secondary">Duration</Typography>
                  <Typography variant="h5" fontWeight={600}>
                    {data.best_window.duration_hours} hours
                  </Typography>
                </Grid>
                <Grid size={{ xs: 12, md: 3 }}>
                  <Typography variant="body2" color="text.secondary">Avg Risk</Typography>
                  <Typography variant="h5" fontWeight={600}>
                    {data.best_window.avg_risk}/100
                  </Typography>
                </Grid>
                <Grid size={{ xs: 12, md: 3 }}>
                  <Typography variant="body2" color="text.secondary">Conditions</Typography>
                  <Typography variant="body1" fontWeight={500}>
                    {data.best_window.conditions}
                  </Typography>
                </Grid>
              </Grid>
            </CardContent>
          </Card>
        </Grid>
      )}

      {/* Hourly Risk Chart */}
      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📊 Hourly Risk Score Trend
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={data.hourly_data}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="hour"
                  tickFormatter={(hour) => `${String(hour).padStart(2, '0')}:00`}
                />
                <YAxis domain={[0, 100]} />
                <Tooltip
                  labelFormatter={(hour) => `${String(hour).padStart(2, '0')}:00`}
                  formatter={(value) => [`${value}/100`, 'Risk Score']}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="risk_score"
                  stroke="#1976d2"
                  strokeWidth={2}
                  name="Risk Score"
                  dot={{ r: 4 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </Grid>

      {/* Hourly Conditions */}
      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              🌡️ Hourly Conditions Detail
            </Typography>
            <Box sx={{ maxHeight: 400, overflow: 'auto' }}>
              <Grid container spacing={1}>
                {data.hourly_data.map((hour, idx) => (
                  <Grid size={{ xs: 12, sm: 6, md: 4 }} key={idx}>
                    <Paper
                      sx={{
                        p: 1.5,
                        bgcolor: hour.is_climbable ? 'success.50' : 'grey.100',
                        border: 1,
                        borderColor: hour.is_climbable ? 'success.main' : 'grey.300',
                      }}
                    >
                      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 0.5 }}>
                        <Typography variant="subtitle2" fontWeight={600}>
                          {String(hour.hour).padStart(2, '0')}:00
                        </Typography>
                        <Chip
                          label={hour.risk_score}
                          size="small"
                          sx={{
                            bgcolor: hour.risk_score < 35 ? 'success.main' :
                                     hour.risk_score < 55 ? 'warning.main' :
                                     hour.risk_score < 75 ? 'warning.dark' : 'error.main',
                            color: 'white',
                            fontWeight: 600,
                          }}
                        />
                      </Box>
                      <Typography variant="caption" display="block" color="text.secondary">
                        {hour.conditions_summary}
                      </Typography>
                      <Typography variant="caption" display="block" sx={{ mt: 0.5 }}>
                        🌡️ {hour.temperature}°C | 💨 {hour.wind_speed} m/s | 🌧️ {hour.precipitation} mm
                      </Typography>
                    </Paper>
                  </Grid>
                ))}
              </Grid>
            </Box>
          </CardContent>
        </Card>
      </Grid>

      {/* All Climbing Windows */}
      {data.climbing_windows && data.climbing_windows.length > 0 && (
        <Grid size={12}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom fontWeight={600}>
                🪟 All Suitable Climbing Windows
              </Typography>
              <List>
                {data.climbing_windows.map((window, idx) => (
                  <React.Fragment key={idx}>
                    <ListItem>
                      <ListItemText
                        primary={
                          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <Typography variant="body1" fontWeight={600}>
                              {String(window.start_hour).padStart(2, '0')}:00 - {String(window.end_hour).padStart(2, '0')}:00
                            </Typography>
                            <Chip
                              label={`${window.avg_risk}/100`}
                              size="small"
                              sx={{
                                bgcolor: window.avg_risk < 35 ? 'success.main' :
                                         window.avg_risk < 55 ? 'warning.main' :
                                         window.avg_risk < 75 ? 'warning.dark' : 'error.main',
                                color: 'white',
                                fontWeight: 600,
                              }}
                            />
                          </Box>
                        }
                        secondary={`${window.duration_hours} hours • ${window.conditions}`}
                      />
                    </ListItem>
                    {idx < data.climbing_windows.length - 1 && <Divider />}
                  </React.Fragment>
                ))}
              </List>
            </CardContent>
          </Card>
        </Grid>
      )}
    </Grid>
  );
}

function AscentsTab({ data, loading, routeData }) {
  if (loading) {
    return <LoadingState message="Loading ascent analytics..." />;
  }

  // Handle error responses
  if (data?.error) {
    return (
      <Alert severity="warning">
        <Typography variant="body2" fontWeight={600}>
          Unable to load ascent data
        </Typography>
        <Typography variant="body2">{data.error}</Typography>
      </Alert>
    );
  }

  // Handle boulder routes (excluded from analytics)
  if (data?.excluded_reason) {
    return (
      <Alert severity="warning">
        <Typography variant="body2" fontWeight={600}>
          Analytics Not Available
        </Typography>
        <Typography variant="body2">
          {data.excluded_reason}. Boulder problems have different risk characteristics
          than roped climbing routes.
        </Typography>
      </Alert>
    );
  }

  // Show DataOnTheWay if no data or no tick data available
  if (!data || data.has_data === false || data.total_ascents === 0) {
    return (
      <DataOnTheWay
        title="Ascent Data on its way!"
        message={data?.message || "We're collecting tick data for this route. Ascent records help calculate accident rates by comparing successful climbs to incidents."}
      />
    );
  }

  return (
    <Grid container spacing={3}>
      {/* Summary Cards */}
      <Grid size={12}>
        <Card elevation={3}>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              🧗 Ascent Analytics for {routeData.name}
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              Monthly breakdown of recorded ascents and accident rates.
            </Typography>
            <Grid container spacing={3} sx={{ mt: 1 }}>
              <Grid size={{ xs: 6, md: 2.4 }}>
                <Paper sx={{ p: 2, bgcolor: 'primary.50', textAlign: 'center' }}>
                  <Typography variant="body2" color="text.secondary">Total Ascents</Typography>
                  <Typography variant="h4" fontWeight={700} color="primary.main">
                    {data.total_ascents}
                  </Typography>
                </Paper>
              </Grid>
              <Grid size={{ xs: 6, md: 2.4 }}>
                <Paper sx={{ p: 2, bgcolor: 'error.50', textAlign: 'center' }}>
                  <Typography variant="body2" color="text.secondary">Total Accidents</Typography>
                  <Typography variant="h4" fontWeight={700} color="error.main">
                    {data.total_accidents}
                  </Typography>
                </Paper>
              </Grid>
              <Grid size={{ xs: 6, md: 2.4 }}>
                <Paper sx={{ p: 2, bgcolor: 'warning.50', textAlign: 'center' }}>
                  <Typography variant="body2" color="text.secondary">Accident Rate</Typography>
                  <Typography variant="h4" fontWeight={700} color="warning.dark">
                    {data.overall_accident_rate}%
                  </Typography>
                  <Typography variant="caption" color="text.secondary">per 100 ascents</Typography>
                </Paper>
              </Grid>
              <Grid size={{ xs: 6, md: 2.4 }}>
                <Paper sx={{ p: 2, bgcolor: 'success.50', textAlign: 'center' }}>
                  <Typography variant="body2" color="text.secondary">Safest Month</Typography>
                  <Typography variant="h5" fontWeight={700} color="success.main">
                    {data.best_month || 'N/A'}
                  </Typography>
                </Paper>
              </Grid>
              <Grid size={{ xs: 6, md: 2.4 }}>
                <Paper sx={{ p: 2, bgcolor: 'info.50', textAlign: 'center' }}>
                  <Typography variant="body2" color="text.secondary">Peak Activity</Typography>
                  <Typography variant="h5" fontWeight={700} color="info.main">
                    {data.peak_month || 'N/A'}
                  </Typography>
                </Paper>
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      </Grid>

      {/* Monthly Breakdown Chart */}
      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📊 Ascents & Accidents by Month
            </Typography>
            <ResponsiveContainer width="100%" height={350}>
              <BarChart data={data.monthly_stats}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" />
                <YAxis yAxisId="left" orientation="left" stroke="#1976d2" />
                <YAxis yAxisId="right" orientation="right" stroke="#f44336" />
                <Tooltip />
                <Legend />
                <Bar
                  yAxisId="left"
                  dataKey="ascent_count"
                  fill="#1976d2"
                  name="Ascents"
                  radius={[4, 4, 0, 0]}
                />
                <Bar
                  yAxisId="right"
                  dataKey="accident_count"
                  fill="#f44336"
                  name="Accidents"
                  radius={[4, 4, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </Grid>

      {/* Monthly Accident Rate List */}
      <Grid size={{ xs: 12, md: 6 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📈 Accident Rate by Month
            </Typography>
            <Box sx={{ maxHeight: 400, overflow: 'auto' }}>
              <List dense>
                {data.monthly_stats?.map((month, idx) => (
                  <React.Fragment key={idx}>
                    <ListItem>
                      <ListItemText
                        primary={
                          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <Typography variant="body1" fontWeight={600}>
                              {month.month}
                            </Typography>
                            <Chip
                              label={month.ascent_count > 0 ? `${month.accident_rate}%` : 'No data'}
                              size="small"
                              sx={{
                                bgcolor: month.ascent_count === 0 ? 'grey.400' :
                                         month.accident_rate === 0 ? 'success.main' :
                                         month.accident_rate < 5 ? 'success.light' :
                                         month.accident_rate < 10 ? 'warning.main' : 'error.main',
                                color: 'white',
                                fontWeight: 600,
                              }}
                            />
                          </Box>
                        }
                        secondary={`${month.ascent_count} ascents • ${month.accident_count} accidents`}
                      />
                    </ListItem>
                    {idx < 11 && <Divider />}
                  </React.Fragment>
                ))}
              </List>
            </Box>
          </CardContent>
        </Card>
      </Grid>

      {/* Best/Worst Month Highlights */}
      <Grid size={{ xs: 12, md: 6 }}>
        <Grid container spacing={2}>
          <Grid size={12}>
            <Paper sx={{ p: 2, bgcolor: 'success.50', borderLeft: 4, borderColor: 'success.main' }}>
              <Typography variant="subtitle2" fontWeight={600} color="success.dark">
                ✅ Safest Month: {data.best_month || 'N/A'}
              </Typography>
              {data.best_month && (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  {data.monthly_stats?.find(m => m.month === data.best_month)?.ascent_count || 0} ascents
                  with {data.monthly_stats?.find(m => m.month === data.best_month)?.accident_count || 0} accidents
                  ({data.monthly_stats?.find(m => m.month === data.best_month)?.accident_rate || 0}% rate)
                </Typography>
              )}
            </Paper>
          </Grid>
          <Grid size={12}>
            <Paper sx={{ p: 2, bgcolor: 'error.50', borderLeft: 4, borderColor: 'error.main' }}>
              <Typography variant="subtitle2" fontWeight={600} color="error.dark">
                ⚠️ Highest Risk Month: {data.worst_month || 'N/A'}
              </Typography>
              {data.worst_month && (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  {data.monthly_stats?.find(m => m.month === data.worst_month)?.ascent_count || 0} ascents
                  with {data.monthly_stats?.find(m => m.month === data.worst_month)?.accident_count || 0} accidents
                  ({data.monthly_stats?.find(m => m.month === data.worst_month)?.accident_rate || 0}% rate)
                </Typography>
              )}
            </Paper>
          </Grid>
          <Grid size={12}>
            <Paper sx={{ p: 2, bgcolor: 'info.50', borderLeft: 4, borderColor: 'info.main' }}>
              <Typography variant="subtitle2" fontWeight={600} color="info.dark">
                📈 Peak Activity Month: {data.peak_month || 'N/A'}
              </Typography>
              {data.peak_month && (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  {data.monthly_stats?.find(m => m.month === data.peak_month)?.ascent_count || 0} recorded ascents
                  — the most popular month for this route
                </Typography>
              )}
            </Paper>
          </Grid>
        </Grid>
      </Grid>

      {/* Disclaimer */}
      <Grid size={12}>
        <Alert severity="info">
          <Typography variant="body2" fontWeight={600}>
            About Accident Rates
          </Typography>
          <Typography variant="body2">
            Accident rate is calculated as (accidents ÷ ascents × 100). A lower rate indicates
            safer conditions. Note that this data is based on reported ascents and accidents only,
            and may not represent all climbing activity on this route.
          </Typography>
        </Alert>
      </Grid>
    </Grid>
  );
}
