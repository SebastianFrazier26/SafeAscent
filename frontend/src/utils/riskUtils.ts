/**
 * Risk interpretation utilities. A missing score is never coerced to a number:
 * callers check isRiskScore and show "Unavailable" instead.
 */

import type { RiskColorCode } from '../services/api';
import { darkenToContrast, readableTextOn } from './color';

export type RiskLevel = 'low' | 'moderate' | 'high' | 'extreme';

/**
 * The only risk-band definition in the frontend (owner decision 2026-09-28: 25/50/75
 * until Phase 3). Edges are lower-inclusive: 25 is moderate. Mirrors
 * RISK_BAND_THRESHOLDS in backend/app/services/risk_bands.py;
 * backend/tests/test_risk_bands.py parses this line and fails if they drift.
 */
export const RISK_BAND_THRESHOLDS = [25, 50, 75] as const;

export interface ConfidenceInfo {
  level: string;
  description: string;
  color: string;
}

export const isRiskScore = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export const getRiskLevel = (riskScore: number): RiskLevel => {
  const [low, moderate, high] = RISK_BAND_THRESHOLDS;
  if (riskScore < low) return 'low';
  if (riskScore < moderate) return 'moderate';
  if (riskScore < high) return 'high';
  return 'extreme';
};

const RISK_COLOR_CODE: Record<RiskLevel, RiskColorCode> = {
  low: 'green',
  moderate: 'yellow',
  high: 'orange',
  extreme: 'red',
};

/** Same colour names the backend emits as color_code, from the same bands. */
export const getRiskColorCode = (riskScore: number): RiskColorCode =>
  RISK_COLOR_CODE[getRiskLevel(riskScore)];

const RISK_BG_CLASS: Record<RiskLevel, string> = {
  low: 'bg-risk-low',
  moderate: 'bg-risk-moderate',
  high: 'bg-risk-high',
  extreme: 'bg-risk-extreme',
};

export const getRiskColor = (riskScore: number): string => RISK_BG_CLASS[getRiskLevel(riskScore)];

const RISK_DESCRIPTIONS: Record<RiskLevel, string> = {
  low: 'Low Risk - Favorable conditions based on historical data',
  moderate: 'Moderate Risk - Exercise caution and prepare accordingly',
  high: 'High Risk - Significant hazards present, reconsider route',
  extreme: 'Extreme Risk - Dangerous conditions, strongly advise against',
};

export const getRiskDescription = (riskScore: number): string =>
  RISK_DESCRIPTIONS[getRiskLevel(riskScore)];

export const getConfidenceInfo = (confidence: number): ConfidenceInfo => {
  if (confidence >= 75) {
    return {
      level: 'High',
      description: 'Prediction based on substantial accident data in this region',
      color: 'text-green-600',
    };
  }
  if (confidence >= 50) {
    return {
      level: 'Medium',
      description: 'Moderate amount of accident data available for this area',
      color: 'text-yellow-600',
    };
  }
  if (confidence >= 25) {
    return {
      level: 'Low',
      description: 'Limited accident data in this region - use caution',
      color: 'text-orange-600',
    };
  }
  return {
    level: 'Very Low',
    description: 'Very limited data - prediction may be unreliable',
    color: 'text-red-600',
  };
};

export const formatRiskScore = (riskScore: number | null | undefined): string =>
  isRiskScore(riskScore) ? `${Math.round(riskScore)}/100` : 'Unavailable';

export const formatConfidence = (confidence: number): string => `${Math.round(confidence)}%`;

/**
 * The only band -> hex palette (controller ruling 2026-09-28: the map marker colours).
 * Map markers, clusters, legend, modal chips and PredictionResult all read it, so one
 * score is one colour everywhere.
 */
export const RISK_COLOR_HEX: Record<RiskColorCode, string> = {
  green: '#4caf50',
  yellow: '#fdd835',
  orange: '#ff9800',
  red: '#f44336',
};

export const NO_RISK_HEX = '#9e9e9e';

// Picked by computed WCAG contrast (>= 4.5:1), not by eye: white on the green, orange and
// red band hexes is under 4:1.
export const RISK_TEXT_ON_HEX = Object.fromEntries(
  Object.entries(RISK_COLOR_HEX).map(([code, hex]) => [code, readableTextOn(hex)]),
) as Record<RiskColorCode, string>;

export const NO_RISK_TEXT_HEX = readableTextOn(NO_RISK_HEX);

// Risk icons sit on white cards; WCAG 1.4.11 wants 3:1 for meaningful graphics and the
// yellow band hex is ~1.4:1 there. Icons use a darkened shade; map colours stay as they are.
const ICON_MIN_CONTRAST = 3;
export const RISK_ICON_HEX = Object.fromEntries(
  Object.entries(RISK_COLOR_HEX).map(([code, hex]) => [code, darkenToContrast(hex, '#ffffff', ICON_MIN_CONTRAST)]),
) as Record<RiskColorCode, string>;

export const NO_RISK_ICON_HEX = darkenToContrast(NO_RISK_HEX, '#ffffff', ICON_MIN_CONTRAST);

export const getMarkerColor = (riskScore: number): string => RISK_COLOR_HEX[getRiskColorCode(riskScore)];
