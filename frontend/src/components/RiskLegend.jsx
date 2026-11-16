/**
 * Map legend: one swatch per risk band, the low-confidence hatch, the no-evidence gray and
 * the unavailable ring, all from the shared palette in riskUtils.
 */
import { Box, Divider, Paper, Typography } from '@mui/material';
import { LEGEND_PANEL_BG, LEGEND_SWATCHES, legendSwatchBorder } from '../utils/riskUtils';
import RiskSwatch from './RiskSwatch';

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
        {LEGEND_SWATCHES.map(({ label, caption, fill, hatch }) => (
          <Box key={label} sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <RiskSwatch
              testId={`legend-swatch-${label}`}
              fill={fill}
              hatch={hatch}
              size={24}
              borderColor={legendSwatchBorder(fill, LEGEND_PANEL_BG)}
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
        📍 <strong>Markers:</strong> Individual routes • Clusters show the average when at least half their routes have a score; otherwise gray if most of the rest lack evidence, or an empty ring if most are unavailable • Hatched: low confidence
      </Typography>
    </Paper>
  );
}
