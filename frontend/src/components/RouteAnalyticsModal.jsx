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
  const [currentTab, setCurrentTab] = useState(0);
  const [loading, setLoading] = useState({});
  const [data, setData] = useState({
    forecast: null,
    routeDetails: null,
    accidents: null,
    breakdown: null,
    seasonal: null,
    historical: null,
    timeOfDay: null,
    ascents: null,
  });
  const [error, setError] = useState(null);
  const [exportMenuAnchor, setExportMenuAnchor] = useState(null);

  // Reset tab when modal opens
  useEffect(() => {
    if (open) {
      setCurrentTab(0);
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
        seasonal: null,
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

          case 4: // Seasonal Patterns
            if (!data.seasonal) {
              setLoading(prev => ({ ...prev, seasonal: true }));
              const response = await fetch(
                `${API_BASE}/mp-routes/${routeData.route_id}/seasonal-patterns`
              );
              if (!response.ok) throw new Error('Failed to fetch seasonal data');
              const seasonalData = await response.json();
              setData(prev => ({ ...prev, seasonal: seasonalData }));
              setLoading(prev => ({ ...prev, seasonal: false }));
            }
            break;

          case 5: // Historical Trends
            if (!data.historical) {
              setLoading(prev => ({ ...prev, historical: true }));
              const response = await fetch(
                `${API_BASE}/mp-routes/${routeData.route_id}/historical-trends?days=30`
              );
              if (!response.ok) throw new Error('Failed to fetch historical data');
              const historicalData = await response.json();
              setData(prev => ({ ...prev, historical: historicalData }));
              setLoading(prev => ({ ...prev, historical: false }));
            }
            break;

          case 6: // Time of Day Analysis
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

          case 7: // Ascent Analytics
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
        setLoading(prev => ({ ...prev, [Object.keys(loading)[0]]: false }));
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
    csv += `Mountain,${routeData.mountain_name}\n`;
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
              {routeData.mountain_name} • {routeData.type} • Grade {routeData.grade}
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
          <Tab label="Seasonal Patterns" />
          <Tab label="Historical Trends" />
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

        {/* Tab 4: Seasonal Patterns */}
        <TabPanel value={currentTab} index={4}>
          <SeasonalTab
            data={data.seasonal}
            loading={loading.seasonal}
            routeData={routeData}
          />
        </TabPanel>

        {/* Tab 5: Historical Trends */}
        <TabPanel value={currentTab} index={5}>
          <HistoricalTab
            data={data.historical}
            loading={loading.historical}
            routeData={routeData}
          />
        </TabPanel>

        {/* Tab 6: Time of Day Analysis */}
        <TabPanel value={currentTab} index={6}>
          <TimeOfDayTab
            data={data.timeOfDay}
            loading={loading.timeOfDay}
            routeData={routeData}
            selectedDate={selectedDate}
          />
        </TabPanel>

        {/* Tab 7: Ascent Analytics */}
        <TabPanel value={currentTab} index={7}>
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
                  {data.today?.temp_high}°F / {data.today?.temp_low}°F
                </Typography>
              </Grid>
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="body2" color="text.secondary">Precipitation</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {data.today?.precip_chance}%
                </Typography>
              </Grid>
              <Grid size={{ xs: 6, md: 3 }}>
                <Typography variant="body2" color="text.secondary">Wind Speed</Typography>
                <Typography variant="h5" fontWeight={600}>
                  {data.today?.wind_speed} mph
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

  return (
    <Grid container spacing={3}>
      <Grid size={{ xs: 12, md: 6 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📋 Basic Information
            </Typography>
            <Grid container spacing={2} sx={{ mt: 1 }}>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Route Name</Typography>
                <Typography variant="body1" fontWeight={500}>{details.name}</Typography>
              </Grid>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Mountain</Typography>
                <Typography variant="body1" fontWeight={500}>{details.mountain_name}</Typography>
              </Grid>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Route Type</Typography>
                <Typography variant="body1" fontWeight={500}>{details.type}</Typography>
              </Grid>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Grade</Typography>
                <Typography variant="body1" fontWeight={500}>{details.grade || 'N/A'}</Typography>
              </Grid>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Elevation</Typography>
                <Typography variant="body1" fontWeight={500}>
                  {details.elevation_meters ? `${Math.round(details.elevation_meters * 3.28084)} ft` : 'N/A'}
                </Typography>
              </Grid>
              <Grid size={6}>
                <Typography variant="body2" color="text.secondary">Location</Typography>
                <Typography variant="body1" fontWeight={500}>
                  {details.latitude?.toFixed(4)}, {details.longitude?.toFixed(4)}
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
              🎯 Difficulty & Commitment
            </Typography>
            <Grid container spacing={2} sx={{ mt: 1 }}>
              <Grid size={12}>
                <Typography variant="body2" color="text.secondary">Technical Grade</Typography>
                <Typography variant="body1" fontWeight={500}>{details.grade || 'Not rated'}</Typography>
              </Grid>
              <Grid size={12}>
                <Typography variant="body2" color="text.secondary">Route Type</Typography>
                <Typography variant="body1" fontWeight={500}>{details.type}</Typography>
              </Grid>
              <Grid size={12}>
                <Typography variant="body2" color="text.secondary">Description</Typography>
                <Typography variant="body2">
                  {details.description || 'No description available'}
                </Typography>
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      </Grid>

      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              🗺️ Location Details
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              This route is located on {details.mountain_name} at coordinates {details.latitude?.toFixed(4)}, {details.longitude?.toFixed(4)}.
              {details.elevation_meters && ` The route reaches an elevation of approximately ${Math.round(details.elevation_meters * 3.28084)} feet.`}
            </Typography>
            {details.approach_notes && (
              <>
                <Typography variant="subtitle2" fontWeight={600} sx={{ mt: 2 }}>
                  Approach Notes:
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {details.approach_notes}
                </Typography>
              </>
            )}
          </CardContent>
        </Card>
      </Grid>
    </Grid>
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
        ⚠️ Accident Reports for {routeData.mountain_name}
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
                border: accident.same_route ? 2 : 0,
                borderColor: accident.same_route ? 'error.main' : 'transparent',
                bgcolor: accident.same_route ? 'error.50' : 'background.paper',
              }}
            >
              <CardContent>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
                  <Box sx={{ flex: 1 }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                      <Typography variant="subtitle1" fontWeight={600}>
                        {accident.date ? format(new Date(accident.date), 'MMM d, yyyy') : 'Date unknown'}
                      </Typography>
                      {accident.same_route && (
                        <Chip
                          label="SAME ROUTE"
                          size="small"
                          color="error"
                          sx={{ fontWeight: 600 }}
                        />
                      )}
                    </Box>
                    <Typography variant="body2" color="text.secondary">
                      Route: {accident.route_name || 'Unknown route'}
                    </Typography>
                  </Box>

                  {/* Impact bar visualization */}
                  <Box sx={{ width: 200, textAlign: 'right' }}>
                    <Typography variant="caption" color="text.secondary">
                      Relevance Score
                    </Typography>
                    <Box
                      sx={{
                        height: 8,
                        bgcolor: 'grey.200',
                        borderRadius: 1,
                        overflow: 'hidden',
                        mt: 0.5,
                      }}
                    >
                      <Box
                        sx={{
                          height: '100%',
                          width: `${accident.impact_score || 50}%`,
                          bgcolor: accident.same_route ? 'error.main' : 'warning.main',
                        }}
                      />
                    </Box>
                  </Box>
                </Box>

                <Typography variant="body2" paragraph>
                  {accident.description || 'No description available'}
                </Typography>

                {accident.weather && (
                  <Paper sx={{ p: 2, bgcolor: 'grey.50' }}>
                    <Typography variant="caption" fontWeight={600} color="text.secondary">
                      Weather Conditions on Accident Date:
                    </Typography>
                    <Grid container spacing={1} sx={{ mt: 0.5 }}>
                      <Grid size={3}>
                        <Typography variant="caption">Temp: {accident.weather.temp}°F</Typography>
                      </Grid>
                      <Grid size={3}>
                        <Typography variant="caption">Wind: {accident.weather.wind_speed} mph</Typography>
                      </Grid>
                      <Grid size={3}>
                        <Typography variant="caption">Precip: {accident.weather.precipitation}%</Typography>
                      </Grid>
                      <Grid size={3}>
                        <Typography variant="caption">
                          {accident.weather.conditions || 'Unknown'}
                        </Typography>
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

function SeasonalTab({ data, loading, routeData: _routeData }) {
  if (loading) {
    return <LoadingState message="Loading seasonal patterns..." />;
  }

  if (!data || !data.monthly_patterns) {
    return (
      <Alert severity="info">
        No seasonal pattern data available.
      </Alert>
    );
  }

  return (
    <Grid container spacing={3}>
      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📅 Seasonal Risk Patterns
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              Historical accident data aggregated by month to identify seasonal trends.
            </Typography>
            <ResponsiveContainer width="100%" height={350}>
              <BarChart data={data.monthly_patterns}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" />
                <YAxis yAxisId="left" orientation="left" stroke="#8884d8" />
                <YAxis yAxisId="right" orientation="right" stroke="#82ca9d" />
                <Tooltip />
                <Legend />
                <Bar
                  yAxisId="left"
                  dataKey="accident_count"
                  fill="#f44336"
                  name="Accidents"
                  radius={[8, 8, 0, 0]}
                />
                <Bar
                  yAxisId="right"
                  dataKey="avg_risk_score"
                  fill="#2196f3"
                  name="Avg Risk Score"
                  radius={[8, 8, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </Grid>

      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              🌡️ Weather Patterns by Month
            </Typography>
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={data.monthly_patterns}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="month" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Area
                  type="monotone"
                  dataKey="avg_temp"
                  stackId="1"
                  stroke="#ff9800"
                  fill="#ff9800"
                  name="Avg Temperature (°F)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </Grid>

      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              💡 Best & Worst Climbing Months
            </Typography>
            <Grid container spacing={2} sx={{ mt: 1 }}>
              <Grid size={{ xs: 12, md: 6 }}>
                <Paper sx={{ p: 2, bgcolor: 'success.50' }}>
                  <Typography variant="subtitle2" fontWeight={600} color="success.dark">
                    ✅ Safest Months
                  </Typography>
                  <List dense>
                    {data.best_months?.map((month, idx) => (
                      <ListItem key={idx}>
                        <ListItemText
                          primary={month.name}
                          secondary={`Avg Risk: ${month.avg_risk}/100 • ${month.accident_count} accidents`}
                        />
                      </ListItem>
                    ))}
                  </List>
                </Paper>
              </Grid>
              <Grid size={{ xs: 12, md: 6 }}>
                <Paper sx={{ p: 2, bgcolor: 'error.50' }}>
                  <Typography variant="subtitle2" fontWeight={600} color="error.dark">
                    ⚠️ Highest Risk Months
                  </Typography>
                  <List dense>
                    {data.worst_months?.map((month, idx) => (
                      <ListItem key={idx}>
                        <ListItemText
                          primary={month.name}
                          secondary={`Avg Risk: ${month.avg_risk}/100 • ${month.accident_count} accidents`}
                        />
                      </ListItem>
                    ))}
                  </List>
                </Paper>
              </Grid>
            </Grid>
          </CardContent>
        </Card>
      </Grid>
    </Grid>
  );
}

function HistoricalTab({ data, loading, routeData: _routeData }) {
  if (loading) {
    return <LoadingState message="Loading historical trends..." />;
  }

  if (!data || !data.historical_predictions) {
    return (
      <Alert severity="info">
        Historical trend data is not yet available for this route. Data collection started recently.
      </Alert>
    );
  }

  return (
    <Grid container spacing={3}>
      <Grid size={12}>
        <Card>
          <CardContent>
            <Typography variant="h6" gutterBottom fontWeight={600}>
              📈 30-Day Risk Score History
            </Typography>
            <Typography variant="body2" color="text.secondary" paragraph>
              Historical risk scores calculated for the past 30 days based on actual weather conditions.
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

  if (!data || !data.hourly_data) {
    return (
      <Alert severity="info">
        Time-of-day analysis not available for this date.
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

  if (!data || !data.has_data) {
    return (
      <Alert severity="info">
        No ascent data available for this route. Ascent records help calculate accident rates
        by comparing successful climbs to incidents.
      </Alert>
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
