import { describe, expect, it } from 'vitest';
import { formatAccidentRate, formatAccidentRateValue, hasAccidentRate } from './accidentRate';

describe('formatAccidentRate', () => {
  it('renders the backend rate as per 1,000 ascents, never a percentage', () => {
    expect(formatAccidentRate(4, 250)).toBe('4 per 1,000 ascents');
    expect(formatAccidentRate(33.33, 30)).toBe('33.33 per 1,000 ascents');
    expect(formatAccidentRate(1500, 2)).toBe('1,500 per 1,000 ascents');
    expect(formatAccidentRate(4, 250)).not.toContain('%');
  });

  it('shows a real zero when there were ascents and no accidents', () => {
    expect(formatAccidentRate(0, 30)).toBe('0 per 1,000 ascents');
  });

  it('never shows the 0.0 the backend sends for a route or month with no ascents', () => {
    expect(formatAccidentRate(0, 0)).toBe('No ascents');
    expect(formatAccidentRateValue(0, 0)).toBe('No ascents');
    expect(hasAccidentRate(0, 0)).toBe(false);
  });

  it('reads Unavailable for a missing or malformed rate', () => {
    expect(formatAccidentRate(undefined, 10)).toBe('Unavailable');
    expect(formatAccidentRate(null)).toBe('Unavailable');
    expect(formatAccidentRate(Number.NaN, 10)).toBe('Unavailable');
    expect(hasAccidentRate(undefined, 10)).toBe(false);
  });
});
