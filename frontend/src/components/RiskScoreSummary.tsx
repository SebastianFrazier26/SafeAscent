/**
 * The route's stored score as the modal shows it: number with its likely range, confidence,
 * evidence and the disclaimer; the gray chip and no number for insufficient evidence; an
 * em-dash for a route the model has no score for.
 */
import type { ReactNode } from 'react';
import { Box, Chip, Paper, Typography } from '@mui/material';
import { SCORE_UNAVAILABLE_MESSAGE } from '../services/api';
import type { SafetyState } from '../hooks/useRouteSafety';
import {
  CONFIDENCE_LABEL,
  INSUFFICIENT_DATA_MESSAGE,
  RISK_DISCLAIMER,
  formatEvidence,
  formatScoreWithRange,
  getRiskDescription,
  markerStyle,
  riskChipStyle,
} from '../utils/riskUtils';
import RiskSwatch from './RiskSwatch';

type Cell = Parameters<typeof markerStyle>[0];

export function SummaryChip({ label, cell }: { label: string; cell: Cell }) {
  const { fill, hatch } = markerStyle(cell);
  const style = riskChipStyle(cell);
  return (
    <Chip
      data-testid="risk-summary-chip"
      data-hatch={String(hatch)}
      icon={hatch ? <RiskSwatch fill={fill} hatch borderColor={style.color} testId="risk-summary-hatch" /> : undefined}
      label={label}
      sx={{ ...style, fontWeight: 600, fontSize: '1rem', height: 'auto', py: 0.5 }}
    />
  );
}

export default function RiskScoreSummary({ safety }: { safety: SafetyState | null | undefined }) {
  if (!safety || safety.status === 'loading') return null;
  // A failed request has its own Alert with Retry; only a 404 is a real "no score" state.
  if (safety.status === 'error' && !safety.unavailable) return null;

  let body: ReactNode;
  if (safety.status === 'error') {
    body = (
      <>
        <SummaryChip label="—" cell={null} />
        <Typography variant="body2" color="text.secondary">{SCORE_UNAVAILABLE_MESSAGE}</Typography>
      </>
    );
  } else if (safety.status === 'insufficient') {
    body = <SummaryChip label={INSUFFICIENT_DATA_MESSAGE} cell={safety.data} />;
  } else {
    const { data } = safety;
    body = (
      <>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <SummaryChip label={formatScoreWithRange(data.risk_score, 'score_range' in data ? data.score_range : null)} cell={data} />
          {'confidence' in data && (
            <Typography variant="body2" fontWeight={600}>{CONFIDENCE_LABEL[data.confidence]}</Typography>
          )}
        </Box>
        <Typography variant="body2">{getRiskDescription(data.risk_score)}</Typography>
        {'evidence' in data && data.evidence && (
          <Typography variant="body2" color="text.secondary">{formatEvidence(data.evidence)}</Typography>
        )}
        <Typography variant="caption" color="text.secondary">{RISK_DISCLAIMER}</Typography>
      </>
    );
  }

  return (
    <Paper data-testid="risk-summary" sx={{ p: 2, mb: 2, display: 'flex', flexDirection: 'column', gap: 1 }}>
      {body}
    </Paper>
  );
}
