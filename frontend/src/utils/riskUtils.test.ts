import { describe, expect, it } from 'vitest';
import {
  CONFIDENCE_LABEL,
  LOWEST_BAND_HEX,
  NO_RISK_HEX,
  RISK_BAND_THRESHOLDS,
  RISK_COLOR_HEX,
  RISK_DISCLAIMER,
  UNAVAILABLE_SAFETY,
  formatEvidence,
  formatRiskScore,
  formatScoreWithRange,
  getRiskDescription,
  markerStyle,
  riskChipStyle,
  getMarkerColor,
  getRiskColor,
  getRiskColorCode,
  getRiskLevel,
  isRiskScore,
} from './riskUtils';
import { contrastRatio } from './color';

describe('isRiskScore', () => {
  it('accepts finite numbers including 0', () => {
    expect(isRiskScore(0)).toBe(true);
    expect(isRiskScore(42.3)).toBe(true);
  });

  it('rejects missing and non-numeric values', () => {
    for (const value of [undefined, null, NaN, Infinity, '42', {}]) {
      expect(isRiskScore(value)).toBe(false);
    }
  });
});

describe('formatRiskScore', () => {
  it('formats a real score, including zero', () => {
    // One decimal everywhere (owner decision 2026-09-28), matching the backend value.
    expect(formatRiskScore(35.5)).toBe('35.5/100');
    expect(formatRiskScore(8.9)).toBe('8.9/100');
    expect(formatRiskScore(30)).toBe('30.0/100');
  });

  it('never invents a number for a missing score', () => {
    expect(formatRiskScore(undefined)).toBe('Unavailable');
    expect(formatRiskScore(null)).toBe('Unavailable');
    expect(formatRiskScore(NaN)).toBe('Unavailable');
  });
});

describe('getRiskLevel', () => {
  it('uses the documented bands', () => {
    expect(getRiskLevel(24.9)).toBe('low');
    expect(getRiskLevel(25)).toBe('moderate');
    expect(getRiskLevel(50)).toBe('high');
    expect(getRiskLevel(75)).toBe('extreme');
  });
});

describe('shared risk bands (owner decision 2026-09-28: 25/50/75)', () => {
  it('exposes the single band definition', () => {
    expect(RISK_BAND_THRESHOLDS).toEqual([25, 50, 75]);
  });

  const cases: Array<[number, string, string]> = [
    [24.99, 'low', 'green'],
    [25, 'moderate', 'yellow'],
    [49.99, 'moderate', 'yellow'],
    [50, 'high', 'orange'],
    [74.99, 'high', 'orange'],
    [75, 'extreme', 'red'],
  ];

  it.each(cases)('score %s -> %s / %s', (score, level, colorCode) => {
    expect(getRiskLevel(score)).toBe(level);
    expect(getRiskColorCode(score)).toBe(colorCode);
  });

  it('bg class and marker colour follow the same level at every edge', () => {
    const markerByLevel = new Map<string, string>();
    for (const [score] of cases) {
      const level = getRiskLevel(score);
      expect(getRiskColor(score)).toBe(`bg-risk-${level}`);
      const marker = getMarkerColor(score);
      expect(markerByLevel.get(level) ?? marker).toBe(marker);
      markerByLevel.set(level, marker);
    }
    expect(new Set(markerByLevel.values()).size).toBe(4);
  });
});

describe('lowest band copy (DP12)', () => {
  it('says lower than typical, never safe or Low Risk', () => {
    const text = getRiskDescription(10);
    expect(text).toContain('Lower than typical');
    expect(text).not.toMatch(/safe/i);
    expect(text).not.toMatch(/low risk/i);
    expect(text).not.toMatch(/favorable/i);
  });

  it('draws the lowest band in a neutral slate, not green and not the no-evidence gray', () => {
    expect(RISK_COLOR_HEX.green).toBe(LOWEST_BAND_HEX);
    expect(LOWEST_BAND_HEX).not.toBe(NO_RISK_HEX);
    expect(LOWEST_BAND_HEX).not.toBe('#4caf50');
  });
});

describe('markerStyle', () => {
  it('hatches a low-confidence score and keeps its band colour', () => {
    expect(markerStyle({ data_status: 'ok', risk_score: 40, confidence: 'low' })).toEqual({
      fill: RISK_COLOR_HEX.yellow,
      hatch: true,
    });
  });

  it.each(['medium', 'high', null] as const)('does not hatch a %s-confidence score', (confidence) => {
    expect(markerStyle({ data_status: 'ok', risk_score: 80, confidence })).toEqual({
      fill: RISK_COLOR_HEX.red,
      hatch: false,
    });
  });

  it('draws insufficient data gray with the neutral hatch', () => {
    expect(markerStyle({ data_status: 'insufficient_data', risk_score: null, confidence: null })).toEqual({
      fill: NO_RISK_HEX,
      hatch: true,
    });
  });

  it('gives an unavailable score no colour at all: not gray, not the 0 band', () => {
    const style = markerStyle({ data_status: null, risk_score: null, confidence: null });
    expect(style).toEqual({ fill: null, hatch: false });
    expect(markerStyle(UNAVAILABLE_SAFETY)).toEqual({ fill: null, hatch: false });
  });
});

describe('score text', () => {
  it('shows the number with its likely range', () => {
    expect(formatScoreWithRange(40, [20, 60])).toBe('40.0 (likely 20.0–60.0)');
    expect(formatScoreWithRange(40, null)).toBe('40.0');
  });

  it('labels confidence by its string level only', () => {
    expect(CONFIDENCE_LABEL).toEqual({ high: 'High confidence', medium: 'Medium confidence', low: 'Low confidence' });
  });

  it('rounds evidence to whole numbers and always shows ≈', () => {
    expect(formatEvidence({ geo: 2.4, similar: 11.6 })).toBe('≈ 2 local, 12 from similar routes elsewhere');
    expect(formatEvidence({ geo: 0, similar: 0.2 })).toBe('≈ 0 local, 0 from similar routes elsewhere');
  });

  it('has the disclaimer', () => {
    expect(RISK_DISCLAIMER).toBe(
      'Based on reported incidents and estimated climbing traffic, not on the current condition of this route.',
    );
  });
});

describe('riskChipStyle', () => {
  const cells = [
    ...[10, 30, 60, 90].map((risk_score) => ({ data_status: 'ok', risk_score, confidence: 'low' })),
    { data_status: 'insufficient_data', risk_score: null },
  ];

  it.each(cells)('text on the flat fill reaches 4.5:1 (%o)', (cell) => {
    const style = riskChipStyle(cell);
    expect(contrastRatio(style.color, style.bgcolor)).toBeGreaterThanOrEqual(4.5);
  });

  it('never puts the hatch behind chip text', () => {
    for (const cell of cells) expect(riskChipStyle(cell)).not.toHaveProperty('backgroundImage');
  });
});
