/**
 * Risk interpretation utilities. A missing score is never coerced to a number:
 * callers check isRiskScore and show "Unavailable" instead.
 */

import type { Confidence, DataStatus, RiskColorCode } from '../services/api';
import { darkenToContrast, readableTextOn } from './color';

export type RiskLevel = 'low' | 'moderate' | 'high' | 'extreme';

/**
 * The only risk-band definition in the frontend (owner decision 2026-09-28: 25/50/75
 * until Phase 3). Edges are lower-inclusive: 25 is moderate. Mirrors
 * RISK_BAND_THRESHOLDS in backend/app/services/risk_bands.py;
 * backend/tests/test_risk_bands.py parses this line and fails if they drift.
 */
export const RISK_BAND_THRESHOLDS = [25, 50, 75] as const;

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

// DP12: the lowest band is a percentile among routes of the same type, not a verdict, so it
// never says "safe" or "Low Risk".
export const LOWEST_BAND_LABEL = 'Lower than typical for this type';

const RISK_DESCRIPTIONS: Record<RiskLevel, string> = {
  low: `${LOWEST_BAND_LABEL} - relative to reported incidents on similar routes`,
  moderate: 'Moderate Risk - Exercise caution and prepare accordingly',
  high: 'High Risk - Significant hazards present, reconsider route',
  extreme: 'Extreme Risk - Dangerous conditions, strongly advise against',
};

export const getRiskDescription = (riskScore: number): string =>
  RISK_DESCRIPTIONS[getRiskLevel(riskScore)];

// One decimal everywhere, the same value the backend stores and colours (owner decision 2026-09-28).
export const formatRiskScore = (riskScore: number | null | undefined): string =>
  isRiskScore(riskScore) ? `${riskScore.toFixed(1)}/100` : 'Unavailable';

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  high: 'High confidence',
  medium: 'Medium confidence',
  low: 'Low confidence',
};

export const isConfidence = (value: unknown): value is Confidence =>
  value === 'high' || value === 'medium' || value === 'low';

const isScoreRange = (range: unknown): range is [number, number] =>
  Array.isArray(range) && range.length === 2 && range.every(isRiskScore);

/** "40.0 (likely 20.0–60.0)"; just the number when there is no range (legacy scores). */
export const formatScoreWithRange = (riskScore: number, range: unknown): string =>
  isScoreRange(range)
    ? `${riskScore.toFixed(1)} (likely ${range[0].toFixed(1)}–${range[1].toFixed(1)})`
    : riskScore.toFixed(1);

// Effective counts are fractional (shrinkage weights), so they are rounded and always hedged.
export const formatEvidence = (evidence: { geo: number; similar: number }): string =>
  `≈ ${Math.round(evidence.geo)} local, ${Math.round(evidence.similar)} from similar routes elsewhere`;

export const RISK_DISCLAIMER =
  'Based on reported incidents and estimated climbing traffic, not on the current condition of this route.';

/**
 * The only band -> hex palette (controller ruling 2026-09-28: the map marker colours).
 * Map markers, clusters, legend and modal chips all read it, so one
 * score is one colour everywhere.
 */
export const LOWEST_BAND_HEX = '#607d8b';

// Keyed by the wire colour_code; 'green' stays the wire value for the lowest band (DP12) so
// the backend contract is unchanged, but it is drawn in neutral slate, not a reassuring green.
export const RISK_COLOR_HEX: Record<RiskColorCode, string> = {
  green: LOWEST_BAND_HEX,
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

/**
 * Insufficient is a stored model state (model.route_scores.insufficient: no reported incidents
 * here or on similar routes to borrow from), so it has no number and is the only gray.
 * Unavailable (no stored row, or a 404) is a different state: no colour, never gray, never 0.
 */
export const INSUFFICIENT_DATA_LABEL = 'Not enough evidence';
export const INSUFFICIENT_DATA_MESSAGE = 'Not enough reported incidents or similar routes to estimate';

export const isInsufficientData = (value: unknown): boolean =>
  typeof value === 'object' && value !== null && (value as { data_status?: unknown }).data_status === 'insufficient_data';

/** Recharts Tooltip formatter for risk series: insufficient points say so, never a number. */
export const formatRiskTooltip = (
  value: unknown,
  name: string,
  item: { payload?: unknown } | undefined,
): [string, string] => [
  isInsufficientData(item?.payload) ? INSUFFICIENT_DATA_LABEL : formatRiskScore(isRiskScore(value) ? value : null),
  name,
];

export interface MarkerStyle {
  fill: string | null;
  hatch: boolean;
}

interface ScoreLike {
  risk_score?: unknown;
  data_status?: unknown;
  confidence?: unknown;
}

/**
 * One rule for every surface that colours a score (map marker, legend, chips): insufficient is
 * gray with the neutral hatch, a score is its band colour (hatched when confidence is low), and
 * anything else is unavailable with no colour at all.
 */
export const markerStyle = (cell: ScoreLike | null | undefined): MarkerStyle => {
  if (cell?.data_status === 'insufficient_data') return { fill: NO_RISK_HEX, hatch: true };
  if (isRiskScore(cell?.risk_score)) {
    return { fill: RISK_COLOR_HEX[getRiskColorCode(cell.risk_score)], hatch: cell.confidence === 'low' };
  }
  return { fill: null, hatch: false };
};

const HATCH_LINE_RGBA: [number, number, number, number] = [33, 33, 33, 110];
const HATCH_LINE_CSS = `rgba(${HATCH_LINE_RGBA.slice(0, 3).join(', ')}, ${(HATCH_LINE_RGBA[3] / 255).toFixed(2)})`;

/** The same diagonal hatch as the map overlay, for HTML swatches and chips. */
export const HATCH_CSS = `repeating-linear-gradient(45deg, ${HATCH_LINE_CSS} 0 2px, transparent 2px 5px)`;

/**
 * Mapbox circle layers take no pattern fill, so the hatch is a circular stripe image drawn
 * over the marker by a symbol layer; raw RGBA keeps it synchronous (no image decode on load).
 */
export const hatchImage = (size: number): { width: number; height: number; data: Uint8Array } => {
  const data = new Uint8Array(size * size * 4);
  const r = size / 2;
  const period = Math.max(4, Math.round(size / 4));
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const dx = x + 0.5 - r;
      const dy = y + 0.5 - r;
      if (dx * dx + dy * dy > r * r) continue;
      if ((x + y) % period >= period / 2) continue;
      data.set(HATCH_LINE_RGBA, (y * size + x) * 4);
    }
  }
  return { width: size, height: size, data };
};

export interface ChipStyle {
  bgcolor: string;
  color: string;
  border?: string;
  backgroundImage?: string;
}

// Loading, failed and unavailable read as "no colour": an outline, never the no-evidence gray.
export const UNAVAILABLE_CHIP_STYLE: ChipStyle = { bgcolor: 'transparent', color: 'inherit', border: '1px dashed' };

export const riskChipStyle = (cell: ScoreLike | null | undefined): ChipStyle => {
  const { fill, hatch } = markerStyle(cell);
  if (fill === null) return UNAVAILABLE_CHIP_STYLE;
  return { bgcolor: fill, color: readableTextOn(fill), ...(hatch ? { backgroundImage: HATCH_CSS } : {}) };
};

export interface RouteSafetyProps {
  risk_score: number | null;
  color_code: RiskColorCode | 'gray' | null;
  data_status: DataStatus | null;
  confidence: Confidence | null;
  hatch: boolean;
}

/** A route the bulk map sent with safety: null (no stored row), or a malformed entry. */
export const UNAVAILABLE_SAFETY: RouteSafetyProps = {
  risk_score: null,
  color_code: null,
  data_status: null,
  confidence: null,
  hatch: false,
};

const inRange = (value: unknown): value is number => isRiskScore(value) && value >= 0 && value <= 100;

/** Map feature properties for one route's embedded bulk safety entry. */
export const routeSafetyProps = (safety: unknown): RouteSafetyProps => {
  if (safety === null || typeof safety !== 'object') return UNAVAILABLE_SAFETY;
  const { risk_score, color_code, data_status, confidence } = safety as Record<string, unknown>;
  if (data_status === 'insufficient_data' && risk_score === null && color_code === 'gray') {
    return { risk_score: null, color_code: 'gray', data_status: 'insufficient_data', confidence: null, hatch: true };
  }
  if (data_status === 'ok' && inRange(risk_score)) {
    const level = isConfidence(confidence) ? confidence : null;
    return {
      risk_score,
      color_code: getRiskColorCode(risk_score),
      data_status: 'ok',
      confidence: level,
      hatch: markerStyle({ data_status, risk_score, confidence: level }).hatch,
    };
  }
  return UNAVAILABLE_SAFETY;
};

// The legend Paper renders on exactly this colour (theme background.paper, with MUI's
// dark-mode elevation overlay switched off), so the computed contrast is the rendered one.
export const LEGEND_PANEL_BG = '#1e1e1e';
const LEGEND_BORDER_MIN_CONTRAST = 3;

// Every map marker's ring; an unavailable route is this ring alone, on the map and in the legend.
export const MARKER_OUTLINE_HEX = '#ffffff';

/** Swatch border that reaches 3:1 on the panel (WCAG 1.4.11); the fill stays the palette hex. */
export const legendSwatchBorder = (fill: string | null, panel: string = LEGEND_PANEL_BG): string =>
  darkenToContrast(fill ?? MARKER_OUTLINE_HEX, panel, LEGEND_BORDER_MIN_CONTRAST);

const [LOW_MAX, MODERATE_MAX, HIGH_MAX] = RISK_BAND_THRESHOLDS;

export interface LegendSwatch {
  label: string;
  caption: string;
  fill: string | null;
  hatch: boolean;
}

export const LEGEND_SWATCHES: readonly LegendSwatch[] = [
  { label: `Lower (0-${LOW_MAX})`, caption: LOWEST_BAND_LABEL, fill: RISK_COLOR_HEX.green, hatch: false },
  { label: `Moderate (${LOW_MAX}-${MODERATE_MAX})`, caption: 'Increased caution', fill: RISK_COLOR_HEX.yellow, hatch: false },
  { label: `Elevated (${MODERATE_MAX}-${HIGH_MAX})`, caption: 'Consider postponing', fill: RISK_COLOR_HEX.orange, hatch: false },
  { label: `High Risk (${HIGH_MAX}+)`, caption: 'Not recommended', fill: RISK_COLOR_HEX.red, hatch: false },
  { label: CONFIDENCE_LABEL.low, caption: 'Any band, hatched: a wide likely range', fill: '#ffffff', hatch: true },
  { label: INSUFFICIENT_DATA_LABEL, caption: INSUFFICIENT_DATA_MESSAGE, fill: NO_RISK_HEX, hatch: true },
  { label: 'Unavailable', caption: 'No score for this route yet', fill: null, hatch: false },
];
