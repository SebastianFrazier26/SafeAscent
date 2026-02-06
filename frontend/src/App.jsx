/**
 * SafeAscent - Main Application (Material Design)
 *
 * Homepage with interactive map and route safety predictions.
 */
import { useState } from 'react';
import {
  Box,
  AppBar,
  Toolbar,
  Typography,
  Container,
  Alert,
  AlertTitle,
  Paper,
  List,
  ListItem,
  ListItemText,
  IconButton,
  Drawer,
} from '@mui/material';
import {
  Terrain as TerrainIcon,
  Close as CloseIcon,
} from '@mui/icons-material';
import MapView from './components/MapView';
import PredictionForm from './components/PredictionForm';
import PredictionResult from './components/PredictionResult';
import { predictRouteSafety } from './services/api';

function App() {
  // State
  const [prediction, setPrediction] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedRouteForZoom, setSelectedRouteForZoom] = useState(null);

  /**
   * Handle prediction form submission
   */
  const handlePredictionSubmit = async (params) => {
    setIsLoading(true);
    setError(null);
    setPrediction(null);

    try {
      console.log('Submitting prediction request:', params);
      const result = await predictRouteSafety(params);
      console.log('Prediction result:', result);
      setPrediction(result);
    } catch (err) {
      console.error('Prediction error:', err);
      setError(err.message || 'Failed to fetch prediction. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Reset and start new prediction
   */
  const handleReset = () => {
    setPrediction(null);
    setError(null);
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Header */}
      <AppBar position="static" elevation={4}>
        <Toolbar>
          <TerrainIcon sx={{ mr: 2, fontSize: 32 }} />
          <Box sx={{ flexGrow: 1 }}>
            <Typography variant="h5" component="h1" fontWeight={600}>
              SafeAscent
            </Typography>
            <Typography variant="caption" sx={{ color: 'primary.50' }}>
              route safety predictions & weather reporting
            </Typography>
          </Box>
          <Box sx={{ display: { xs: 'none', md: 'flex' }, alignItems: 'center', gap: 2 }}>
            <Box
              sx={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                bgcolor: 'success.light',
                animation: 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
                '@keyframes pulse': {
                  '0%, 100%': { opacity: 1 },
                  '50%': { opacity: 0.5 },
                },
              }}
            />
            <Typography variant="body2">Live</Typography>
          </Box>
        </Toolbar>
      </AppBar>

      {/* Main Content */}
      <Box sx={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Map Section (Left) */}
        <Box sx={{ flex: 1, position: 'relative' }}>
          <MapView selectedRouteForZoom={selectedRouteForZoom} />
        </Box>

        {/* Sidebar (Right) */}
        <Drawer
          variant="permanent"
          anchor="right"
          sx={{
            width: 400,
            flexShrink: 0,
            '& .MuiDrawer-paper': {
              width: 400,
              position: 'relative',
              boxSizing: 'border-box',
              borderLeft: 1,
              borderColor: 'divider',
            },
          }}
        >
          <Box sx={{ overflow: 'auto', p: 3 }}>
            {/* Show form if no prediction */}
            {!prediction && (
              <PredictionForm
                onSubmit={handlePredictionSubmit}
                isLoading={isLoading}
                onRouteSelect={setSelectedRouteForZoom}
              />
            )}

            {/* Show results if prediction available */}
            {prediction && (
              <PredictionResult
                prediction={prediction}
                onReset={handleReset}
              />
            )}

            {/* Error Display */}
            {error && (
              <Alert
                severity="error"
                onClose={() => setError(null)}
                sx={{ mt: 3, mb: 3 }}
              >
                <AlertTitle>Error</AlertTitle>
                {error}
              </Alert>
            )}

            {/* Help Section */}
            {!prediction && !error && (
              <Paper
                elevation={0}
                sx={{
                  mt: 3,
                  p: 2.5,
                  bgcolor: 'primary.50',
                  border: 1,
                  borderColor: 'primary.200',
                }}
              >
                <Typography variant="subtitle1" fontWeight={600} color="primary.main" gutterBottom>
                  How It Works
                </Typography>
                <List dense sx={{ pt: 1 }}>
                  <ListItem sx={{ px: 0 }}>
                    <ListItemText
                      primary="1. Search for a route by name or ID, or click a route marker on the map"
                      primaryTypographyProps={{
                        variant: 'body2',
                        color: 'text.primary',
                      }}
                    />
                  </ListItem>
                  <ListItem sx={{ px: 0 }}>
                    <ListItemText
                      primary="2. Select your planned date (next 3 days)"
                      primaryTypographyProps={{
                        variant: 'body2',
                        color: 'text.primary',
                      }}
                    />
                  </ListItem>
                </List>
              </Paper>
            )}
          </Box>
        </Drawer>
      </Box>

      {/* Footer */}
      <Box
        component="footer"
        sx={{
          py: 1.5,
          px: 2,
          textAlign: 'center',
          bgcolor: 'grey.900',
          color: 'grey.300',
          borderTop: 1,
          borderColor: 'divider',
        }}
      >
        <Typography variant="caption">
          SafeAscent © 2026 • SafeAscent is a tool to assist climbers with route-selection & preparation - it is NEVER a replacement for good judgement and experience
        </Typography>
      </Box>
    </Box>
  );
}

export default App;
