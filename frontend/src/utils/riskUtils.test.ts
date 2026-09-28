import { describe, expect, it } from 'vitest';
import { formatRiskScore, getRiskLevel, isRiskScore } from './riskUtils';

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
