import { describe, expect, it } from 'vitest';
import {
  LOGGED_ASCENTS_NOTE,
  formatAccidentAscentCounts,
  formatAccidents,
  formatCount,
  formatAccidentsWithoutAscents,
  formatDataSpans,
  formatUndated,
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

  it("states each side's year span, dropping an empty side", () => {
    expect(formatDataSpans({ first: 1990, last: 2023 }, { first: 2016, last: 2026 }))
      .toBe('Accidents: all recorded years (1990–2023). Logged ascents: 2016–2026.');
    expect(formatDataSpans({ first: 2019, last: 2019 }, null))
      .toBe('Accidents: all recorded years (2019).');
    expect(formatDataSpans(null, { first: 2024, last: 2025 })).toBe('Logged ascents: 2024–2025.');
    expect(formatDataSpans(null, undefined)).toBeNull();
    expect(formatDataSpans({ first: 2020 }, { first: 2025, last: 2020 })).toBeNull();
  });
});

describe('undated records', () => {
  it('formatUndated names a positive count and nothing else', () => {
    expect(formatUndated(2)).toBe('(+2 undated)');
    expect(formatUndated(0)).toBeNull();
    for (const bad of [undefined, null, -1, 1.5, '2', NaN]) expect(formatUndated(bad)).toBeNull();
  });

  it('spans carry the undated count, and an all-undated side still shows', () => {
    expect(formatDataSpans({ first: 1998, last: 2021 }, { first: 2024, last: 2026 }, 1, 2))
      .toBe('Accidents: all recorded years (1998–2021) (+1 undated). Logged ascents: 2024–2026 (+2 undated).');
    expect(formatDataSpans(null, { first: 2024, last: 2024 }, 3, 0))
      .toBe('Accidents: none dated (+3 undated). Logged ascents: 2024.');
    expect(formatDataSpans(null, null, 0, undefined)).toBeNull();
  });
});

describe('formatAccidentsWithoutAscents', () => {
  it('keeps the accident count visible', () => {
    expect(formatAccidentsWithoutAscents(3)).toBe('3 accidents · no logged ascents yet');
    expect(formatAccidentsWithoutAscents(1)).toBe('1 accident · no logged ascents yet');
    expect(formatAccidentsWithoutAscents(undefined)).toBe('— accidents · no logged ascents yet');
  });
});
