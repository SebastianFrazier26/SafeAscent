import type { RiskColorCode } from '../services/api';
import { RISK_BAND_THRESHOLDS, getRiskColorCode } from './riskUtils';

/**
 * Owner decision 2026-09-28: a cluster is gray unless at least half its routes have a score.
 * Averaging only the scored ones let 1 low score among 49 insufficient routes paint the whole
 * cluster green.
 */
export const CLUSTER_MIN_SCORED_FRACTION = 0.5;

export const clusterColorCode = (
  scoredCount: number,
  totalCount: number,
  scoreSum: number,
): RiskColorCode | 'gray' => {
  if (scoredCount <= 0 || totalCount <= 0 || scoredCount / totalCount < CLUSTER_MIN_SCORED_FRACTION) {
    return 'gray';
  }
  return getRiskColorCode(scoreSum / scoredCount);
};

const [LOW_MAX, MODERATE_MAX, HIGH_MAX] = RISK_BAND_THRESHOLDS;

/**
 * The same rule as clusterColorCode, as a Mapbox expression over the cluster's point_count
 * (every route) and the risk_score_sum/risk_score_count clusterProperties (scored routes only).
 * Fill and count label pass parallel tables so the label always matches the fill it sits on.
 */
export const clusterColorExpression = (
  byBand: Record<RiskColorCode, string>,
  none: string,
): unknown[] => [
  'case',
  [
    'all',
    ['>', ['get', 'risk_score_count'], 0],
    ['>=', ['/', ['get', 'risk_score_count'], ['get', 'point_count']], CLUSTER_MIN_SCORED_FRACTION],
  ],
  [
    'step',
    ['/', ['get', 'risk_score_sum'], ['get', 'risk_score_count']],
    byBand.green,
    LOW_MAX, byBand.yellow,
    MODERATE_MAX, byBand.orange,
    HIGH_MAX, byBand.red,
  ],
  none,
];
