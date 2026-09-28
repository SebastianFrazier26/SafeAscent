/**
 * Risk interpretation utilities. A missing score is never coerced to a number:
 * callers check isRiskScore and show "Unavailable" instead.
 */

export type RiskLevel = 'low' | 'moderate' | 'high' | 'extreme';

export interface ConfidenceInfo {
  level: string;
  description: string;
  color: string;
}

export const isRiskScore = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export const getRiskLevel = (riskScore: number): RiskLevel => {
  if (riskScore < 25) return 'low';
  if (riskScore < 50) return 'moderate';
  if (riskScore < 75) return 'high';
  return 'extreme';
};

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

const MARKER_COLORS: Record<RiskLevel, string> = {
  low: '#10b981',
  moderate: '#f59e0b',
  high: '#ef4444',
  extreme: '#7c2d12',
};

export const getMarkerColor = (riskScore: number): string => MARKER_COLORS[getRiskLevel(riskScore)];
