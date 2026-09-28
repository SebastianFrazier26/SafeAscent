/**
 * PredictionResult Component - Material Design
 *
 * Displays the safety prediction, or the request error with a Retry action.
 */
import {
  Alert,
  AlertTitle,
  Card,
  CardContent,
  Typography,
  Box,
  Paper,
  Chip,
  Button,
  IconButton,
  Divider,
  Stack,
} from '@mui/material';
import {
  Warning as WarningIcon,
  CheckCircle as CheckCircleIcon,
  Close as CloseIcon,
  Error as ErrorIcon,
  HelpOutline as HelpOutlineIcon,
  Print as PrintIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import {
  INSUFFICIENT_DATA_LABEL,
  INSUFFICIENT_DATA_MESSAGE,
  NO_RISK_ICON_HEX,
  RISK_COLOR_HEX,
  RISK_ICON_HEX,
  RISK_TEXT_ON_HEX,
  getRiskColorCode,
  getRiskLevel,
  getRiskDescription,
  isInsufficientData,
  isRiskScore,
} from '../utils/riskUtils';

function RiskIcon({ level }) {
  if (level === null) return <HelpOutlineIcon sx={{ fontSize: 40 }} />;
  if (level === 'low') return <CheckCircleIcon sx={{ fontSize: 40 }} />;
  if (level === 'moderate') return <WarningIcon sx={{ fontSize: 40 }} />;
  return <ErrorIcon sx={{ fontSize: 40 }} />;
}

/**
 * @param {Object|null} prediction - Prediction result from API
 * @param {Function} onReset - Start a new prediction
 * @param {string|null} [error] - Request error message; takes precedence over prediction
 * @param {Function} [onRetry] - Re-send the last request
 */
export default function PredictionResult({ prediction, onReset, error, onRetry }) {
  if (error) {
    return (
      <Alert
        severity="error"
        onClose={onReset}
        sx={{ mt: 3, mb: 3 }}
        // MUI drops its own close button whenever `action` is set, so the Retry case adds one.
        action={onRetry ? (
          <>
            <Button color="inherit" size="small" onClick={onRetry}>
              Retry
            </Button>
            {onReset && (
              <IconButton color="inherit" size="small" aria-label="Close" onClick={onReset}>
                <CloseIcon fontSize="small" />
              </IconButton>
            )}
          </>
        ) : undefined}
      >
        <AlertTitle>Prediction failed</AlertTitle>
        {error}
      </Alert>
    );
  }

  if (!prediction) return null;

  const insufficient = isInsufficientData(prediction);
  const riskScore = !insufficient && isRiskScore(prediction.risk_score) ? prediction.risk_score : null;
  const riskLevel = riskScore === null ? null : getRiskLevel(riskScore);
  const riskColorCode = riskScore === null ? null : getRiskColorCode(riskScore);

  return (
    <Card elevation={3}>
      <CardContent>
        <Typography variant="h5" component="h2" gutterBottom fontWeight={500} textAlign="center">
          Route Safety Prediction
        </Typography>

        <Box sx={{ textAlign: 'center', my: 4 }}>
          <Box
            data-testid="risk-icon"
            sx={{ color: riskColorCode ? RISK_ICON_HEX[riskColorCode] : NO_RISK_ICON_HEX, mb: 2 }}
          >
            <RiskIcon level={riskLevel} />
          </Box>

          <Typography variant="h2" component="div" fontWeight={700} gutterBottom>
            {insufficient ? INSUFFICIENT_DATA_LABEL : riskScore === null ? 'Unavailable' : riskScore.toFixed(1)}
          </Typography>
          {insufficient && (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 2, maxWidth: 400, mx: 'auto' }}>
              {INSUFFICIENT_DATA_MESSAGE}
            </Typography>
          )}
          {riskScore !== null && (
            <>
              <Typography variant="subtitle1" color="text.secondary" gutterBottom>
                out of 100
              </Typography>
              <Chip
                label={`${riskLevel.toUpperCase()} RISK`}
                sx={{
                  bgcolor: RISK_COLOR_HEX[riskColorCode],
                  color: RISK_TEXT_ON_HEX[riskColorCode],
                  mt: 2,
                  px: 2,
                  py: 1,
                  fontSize: '1rem',
                  fontWeight: 600,
                }}
              />
              <Typography variant="body2" color="text.secondary" sx={{ mt: 2, maxWidth: 400, mx: 'auto' }}>
                {getRiskDescription(riskScore)}
              </Typography>
            </>
          )}
        </Box>

        <Divider sx={{ my: 3 }} />

        {!insufficient && prediction.top_contributing_accidents && prediction.top_contributing_accidents.length > 0 && (
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
