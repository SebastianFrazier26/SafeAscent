/**
 * The Ascents tab headline: the route's rate relative to the national reported-incident rate
 * for its type and month, then a per-10,000-logged-ascents line when the backend sent one.
 * Every figure is the stored one; nothing here is derived from the accident or ascent counts.
 */
import { Box, Typography } from '@mui/material';
import { SCORE_UNAVAILABLE_MESSAGE, type Interval } from '../services/api';
import type { SafetyState } from '../hooks/useRouteSafety';
import { CONFIDENCE_LABEL, INSUFFICIENT_DATA_MESSAGE, isConfidence, markerStyle } from '../utils/riskUtils';
import RiskSwatch from './RiskSwatch';
import { SummaryChip } from './RiskScoreSummary';

const NATIONAL_COMPARISON_UNAVAILABLE = 'National comparison unavailable';

const PER_10K_REASON_MESSAGE: Record<string, string> = {
  ticks_incomplete: 'Logged ascents still being collected for this area',
  no_ticks_logged: 'No logged ascents for this route type here',
  ticks_missing: 'Logged ascents unavailable',
};

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const monthName = (isoDate: unknown): string | null => {
  const m = typeof isoDate === 'string' ? /^\d{4}-(\d{2})-\d{2}/.exec(isoDate) : null;
  return m ? (MONTHS[Number(m[1]) - 1] ?? null) : null;
};

/** One decimal from 1 up, two significant figures below 1, so a small positive value never reads as 0. */
const formatSmallSafe = (value: number): string => {
  if (value === 0) return '0';
  if (value >= 100) return Math.round(value).toLocaleString('en-US');
  if (value >= 1) return value.toFixed(1);
  if (value < 0.001) return '<0.001';
  return String(Number(value.toPrecision(2)));
};

const range = (i: Interval) => `${formatSmallSafe(i.lo)}–${formatSmallSafe(i.hi)}`;

interface Props {
  safety: SafetyState | null | undefined;
  routeType?: string | null;
  selectedDate?: string;
}

export default function AscentsHeadline({ safety, routeType, selectedDate }: Props) {
  if (!safety || safety.status === 'loading') return null;
  if (safety.status === 'error') {
    return safety.unavailable
      ? (
        <Box data-testid="ascents-headline" sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
          <SummaryChip label="—" cell={null} />
          <Typography variant="body2" color="text.secondary">{SCORE_UNAVAILABLE_MESSAGE}</Typography>
        </Box>
      )
      : null;
  }
  if (safety.status === 'insufficient') {
    return (
      <Box data-testid="ascents-headline" sx={{ mb: 2 }}>
        <SummaryChip label={INSUFFICIENT_DATA_MESSAGE} cell={safety.data} />
      </Box>
    );
  }

  const { data } = safety;
  const rate = 'relative_rate' in data ? data.relative_rate : null;
  const per10k = 'per_10k_logged_ascents' in data ? data.per_10k_logged_ascents : null;
  const reason = 'per_10k_reason' in data ? data.per_10k_reason : null;
  const confidence = 'confidence' in data && isConfidence(data.confidence) ? CONFIDENCE_LABEL[data.confidence] : null;
  const { fill, hatch } = markerStyle(data);

  if (!rate) {
    return (
      <Box data-testid="ascents-headline" sx={{ mb: 2 }}>
        <Typography variant="body1" fontWeight={600}>{NATIONAL_COMPARISON_UNAVAILABLE}</Typography>
      </Box>
    );
  }

  const type = routeType?.trim().toLowerCase();
  const month = monthName(selectedDate);
  const headline =
    `${formatSmallSafe(rate.mean)}× (${range(rate)}×) the reported-incident rate of `
    + `${type ? `${type} routes` : 'routes of this type'} nationally${month ? ` in ${month}` : ''}`;
  const reasonLine = reason ? PER_10K_REASON_MESSAGE[reason] : undefined;

  return (
    <Box data-testid="ascents-headline" sx={{ mb: 2, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        {hatch && <RiskSwatch fill={fill} hatch testId="ascents-headline-hatch" />}
        <Typography variant="h6" fontWeight={600}>{headline}</Typography>
      </Box>
      {confidence && <Typography variant="body2" fontWeight={600}>{confidence}</Typography>}
      {per10k ? (
        <Typography variant="body2" data-testid="ascents-per10k">
          {`About ${formatSmallSafe(per10k.mean)} per 10,000 logged ascents (likely ${range(per10k)})`}
          {confidence ? ` · ${confidence}` : ''}
        </Typography>
      ) : (
        reasonLine && <Typography variant="body2" color="text.secondary" data-testid="ascents-per10k-reason">{reasonLine}</Typography>
      )}
    </Box>
  );
}
