import { describe, expect, it } from 'vitest';
import {
  LOGGED_ASCENTS_NOTE,
  formatAccidentAscentCounts,
  formatAccidents,
  formatCount,
  formatLoggedAscents,
} from './ascentCounts';

describe('formatAccidentAscentCounts', () => {
  it('shows plain counts and always says logged ascents', () => {
    expect(formatAccidentAscentCounts(2, 13)).toBe('2 accidents · 13 logged ascents');
    expect(formatAccidentAscentCounts(0, 1250)).toBe('0 accidents · 1,250 logged ascents');
  });

  it('uses the singular for one', () => {
    expect(formatAccidentAscentCounts(1, 1)).toBe('1 accident · 1 logged ascent');
    expect(formatLoggedAscents(1)).toBe('1 logged ascent');
    expect(formatAccidents(1)).toBe('1 accident');
  });

  it('never shows a rate, percentage, or per-N unit', () => {
    const text = formatAccidentAscentCounts(3, 5);
    expect(text).not.toMatch(/%|per\s|rate/i);
  });

  it('renders a missing or malformed count as an em-dash, never 0', () => {
    expect(formatCount(undefined)).toBe('—');
    expect(formatCount(null)).toBe('—');
    expect(formatCount(Number.NaN)).toBe('—');
    expect(formatCount(-1)).toBe('—');
    expect(formatCount(2.5)).toBe('—');
    expect(formatAccidentAscentCounts(undefined, 10)).toBe('— accidents · 10 logged ascents');
  });

  it('note names the undercount and defers the rate to Phase 3', () => {
    expect(LOGGED_ASCENTS_NOTE).toMatch(/logged ascents/);
    expect(LOGGED_ASCENTS_NOTE).toMatch(/undercount/);
    expect(LOGGED_ASCENTS_NOTE).toMatch(/Phase 3/);
  });
});
