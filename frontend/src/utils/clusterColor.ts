import type { RiskColorCode } from '../services/api';
import { RISK_BAND_THRESHOLDS, getRiskColorCode } from './riskUtils';

/**
 * Owner decision 2026-09-28: a cluster takes its scored routes' mean band only when at least
 * half its routes have a score; averaging only the scored ones let 1 low score among 49
 * unscored routes paint the whole cluster. Below half, it shows whichever no-score state
 * dominates: gray (no evidence) only when insufficient routes are at least as many as
 * unavailable ones, else the unavailable ring, since gray asserts "no evidence" and an
 * unavailable route has not been shown to lack it.
 */
export const CLUSTER_MIN_SCORED_FRACTION = 0.5;

export type ClusterColorCode = RiskColorCode | 'gray' | 'none';

export const clusterColorCode = (
  scoredCount: number,
  insufficientCount: number,
  totalCount: number,
  scoreSum: number,
): ClusterColorCode => {
  if (scoredCount > 0 && totalCount > 0 && scoredCount / totalCount >= CLUSTER_MIN_SCORED_FRACTION) {
    return getRiskColorCode(scoreSum / scoredCount);
  }
  const unavailableCount = totalCount - scoredCount - insufficientCount;
  return insufficientCount >= unavailableCount ? 'gray' : 'none';
};

const [LOW_MAX, MODERATE_MAX, HIGH_MAX] = RISK_BAND_THRESHOLDS;

/**
 * The same rule as clusterColorCode, as a Mapbox expression over the cluster's point_count
 * (every route) and the risk_score_sum/risk_score_count/insufficient_count clusterProperties.
 * Fill and count label pass parallel tables so the label always matches the fill it sits on.
 */
export const clusterColorExpression = (
  byBand: Record<RiskColorCode, string>,
  gray: string,
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
  [
    '>=',
    ['get', 'insufficient_count'],
    ['-', ['-', ['get', 'point_count'], ['get', 'risk_score_count']], ['get', 'insufficient_count']],
  ],
  gray,
  none,
];
