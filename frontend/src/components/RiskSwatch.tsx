/**
 * A round risk swatch: the flat fill (none for unavailable) and, when hatched, an SVG stripe
 * pattern over it. Chips carry it as a leading dot so the stripes never sit behind text.
 */
import { useId } from 'react';
import { Box } from '@mui/material';
import { HATCH_LINE_CSS, MARKER_OUTLINE_HEX } from '../utils/riskUtils';

interface RiskSwatchProps {
  fill: string | null;
  hatch: boolean;
  size?: number;
  borderColor?: string;
  testId?: string;
}

export default function RiskSwatch({ fill, hatch, size = 16, borderColor = MARKER_OUTLINE_HEX, testId }: RiskSwatchProps) {
  const patternId = `risk-hatch-${useId().replace(/:/g, '')}`;
  return (
    <Box
      component="span"
      data-testid={testId}
      data-hatch={String(hatch)}
      sx={{
        display: 'inline-block',
        position: 'relative',
        flexShrink: 0,
        width: size,
        height: size,
        borderRadius: '50%',
        overflow: 'hidden',
        bgcolor: fill ?? 'transparent',
        border: fill === null ? '1px dashed' : '2px solid',
        borderColor,
      }}
    >
      {hatch && (
        <svg width="100%" height="100%" style={{ position: 'absolute', inset: 0 }} aria-hidden="true">
          <defs>
            <pattern id={patternId} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="2" height="5" fill={HATCH_LINE_CSS} />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill={`url(#${patternId})`} />
        </svg>
      )}
    </Box>
  );
}
