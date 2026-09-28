/**
 * Map legend: one swatch per risk band plus the insufficient-data gray, all from the
 * shared palette in riskUtils.
 */
import { Box, Divider, Paper, Typography } from '@mui/material';
import { LEGEND_PANEL_BG, LEGEND_SWATCHES, legendSwatchBorder } from '../utils/riskUtils';

export default function RiskLegend() {
  return (
    <Paper
      elevation={3}
      sx={{
        position: 'absolute',
        bottom: 40,
        left: 16,
        p: 2,
        zIndex: 1,
        bgcolor: LEGEND_PANEL_BG,
        // MUI's dark-mode elevation overlay would lighten the panel away from the colour
        // the swatch-border contrast is computed against.
        backgroundImage: 'none',
        borderRadius: 2,
        minWidth: 200,
      }}
    >
      <Typography variant="subtitle2" fontWeight={600} gutterBottom sx={{ mb: 1.5 }}>
        🎯 Safety Score Legend
      </Typography>

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {LEGEND_SWATCHES.map(({ label, caption, fill }) => (
          <Box key={label} sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              data-testid={`legend-swatch-${label}`}
              sx={{
                width: 24,
                height: 24,
                borderRadius: '50%',
                bgcolor: fill,
                border: '2px solid',
                borderColor: legendSwatchBorder(fill, LEGEND_PANEL_BG),
                boxShadow: 1,
              }}
            />
            <Box>
              <Typography variant="body2" fontWeight={500}>
                {label}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {caption}
              </Typography>
            </Box>
          </Box>
        ))}
      </Box>

      <Divider sx={{ my: 1.5 }} />

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
        💡 <strong>Heatmap:</strong> Regional risk coverage across entire map
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        📍 <strong>Markers:</strong> Individual routes • Clusters show average score, gray when fewer than half their routes have one
      </Typography>
    </Paper>
  );
}
