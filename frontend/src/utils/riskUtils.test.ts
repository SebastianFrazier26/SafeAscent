import { describe, expect, it } from 'vitest';
import {
  RISK_BAND_THRESHOLDS,
  formatRiskScore,
  getMarkerColor,
  getRiskColor,
  getRiskColorCode,
  getRiskLevel,
  isRiskScore,
} from './riskUtils';

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
    expect(formatRiskScore(35.5)).toBe('36/100');
    expect(formatRiskScore(0)).toBe('0/100');
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
