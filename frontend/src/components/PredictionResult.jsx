/**
 * PredictionResult Component - Material Design
 *
 * Displays the safety prediction results including risk score and contributing factors.
 */
import {
  Card,
  CardContent,
  Typography,
  Box,
  Paper,
  Chip,
  Button,
  Divider,
  Stack,
} from '@mui/material';
import {
  Warning as WarningIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Print as PrintIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import {
  getRiskLevel,
  getRiskDescription,
} from '../utils/riskUtils';

/**
 * PredictionResult - Display prediction results
 *
 * @param {Object} prediction - Prediction result from API
 * @param {Function} onReset - Callback to reset and start new prediction
 */
export default function PredictionResult({ prediction, onReset }) {
  if (!prediction) return null;

  const riskLevel = getRiskLevel(prediction.risk_score);
  const riskDescription = getRiskDescription(prediction.risk_score);

  // Get risk color based on level
  const getRiskColor = () => {
    if (riskLevel === 'low') return 'success';
    if (riskLevel === 'moderate') return 'warning';
    if (riskLevel === 'high') return 'error';
    return 'error';
  };

  // Get risk icon
  const getRiskIcon = () => {
    if (riskLevel === 'low') return <CheckCircleIcon sx={{ fontSize: 40 }} />;
    if (riskLevel === 'moderate') return <WarningIcon sx={{ fontSize: 40 }} />;
    return <ErrorIcon sx={{ fontSize: 40 }} />;
  };

  const riskColor = getRiskColor();

  return (
    <Card elevation={3}>
      <CardContent>
        <Typography variant="h5" component="h2" gutterBottom fontWeight={500} textAlign="center">
          Route Safety Prediction
        </Typography>

        {/* Risk Score Display */}
        <Box sx={{ textAlign: 'center', my: 4 }}>
          {/* Icon */}
          <Box sx={{ color: `${riskColor}.main`, mb: 2 }}>
            {getRiskIcon()}
          </Box>

          {/* Score */}
          <Typography variant="h2" component="div" fontWeight={700} gutterBottom>
            {Math.round(prediction.risk_score)}
          </Typography>
          <Typography variant="subtitle1" color="text.secondary" gutterBottom>
            out of 100
          </Typography>

          {/* Risk Level Badge */}
          <Chip
            label={`${riskLevel.toUpperCase()} RISK`}
            color={riskColor}
            sx={{
              mt: 2,
              px: 2,
              py: 1,
              fontSize: '1rem',
              fontWeight: 600,
            }}
          />

          {/* Risk Description */}
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2, maxWidth: 400, mx: 'auto' }}>
            {riskDescription}
          </Typography>
        </Box>

        <Divider sx={{ my: 3 }} />

        {/* Top Contributing Accidents */}
        {prediction.top_contributing_accidents && prediction.top_contributing_accidents.length > 0 && (
          <Box sx={{ mt: 3 }}>
            <Typography variant="subtitle1" fontWeight={500} gutterBottom>
              Top Contributing Factors
            </Typography>
            <Stack spacing={1} sx={{ mt: 1.5 }}>
              {prediction.top_contributing_accidents.slice(0, 3).map((accident, idx) => (
                <Paper
                  key={accident.accident_id}
                  elevation={0}
                  sx={{
                    p: 1.5,
                    border: 1,
                    borderColor: 'grey.300',
                    borderRadius: 2,
                  }}
                >
                  <Stack direction="row" justifyContent="space-between" alignItems="flex-start" sx={{ mb: 0.5 }}>
                    <Typography variant="body2" fontWeight={500} color="text.primary">
                      Accident #{idx + 1}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {accident.distance_km.toFixed(1)} km away
                    </Typography>
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {accident.days_ago} days ago • Influence: {(accident.total_influence * 100).toFixed(1)}%
                  </Typography>
                </Paper>
              ))}
            </Stack>
          </Box>
        )}

        {/* Metadata */}
        {prediction.metadata && (
          <Paper elevation={0} sx={{ p: 2, mt: 3, bgcolor: 'grey.50' }}>
            <Typography variant="caption" fontWeight={500} color="text.secondary" display="block" gutterBottom>
              Prediction Details
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block">
              Route type: {prediction.metadata.route_type || 'N/A'}
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block">
              Search date: {prediction.metadata.search_date || 'N/A'}
            </Typography>
            {prediction.metadata.vectorized && (
              <Typography variant="caption" color="primary.main" display="block" sx={{ mt: 0.5 }}>
                ⚡ Optimized computation
              </Typography>
            )}
          </Paper>
        )}

        {/* Action Buttons */}
        <Stack direction="row" spacing={2} sx={{ mt: 3 }}>
          <Button
            variant="outlined"
            startIcon={<RefreshIcon />}
            onClick={onReset}
            fullWidth
          >
            New Prediction
          </Button>
          <Button
            variant="contained"
            startIcon={<PrintIcon />}
            onClick={() => window.print()}
            fullWidth
          >
            Print Report
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
}
