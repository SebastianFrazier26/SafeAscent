/**
 * The Ascents tab shows plain counts, never an accidents-per-ascents rate: MP ticks are a
 * capped, recent, self-reported sample, so a raw ratio would misstate risk. A shrunk rate
 * (per 10,000 logged ascents) comes with the Phase 3 model.
 */

export const LOGGED_ASCENTS_NOTE =
  'Counts from Mountain Project logged ascents, which undercount real ascents. '
  + 'A rate estimate comes with the Phase 3 model.';

const isCount = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0;

/** A missing or malformed count renders as an em-dash, never as 0. */
export const formatCount = (value: unknown): string =>
  (isCount(value) ? value.toLocaleString('en-US') : '—');

const withNoun = (value: unknown, singular: string, plural: string): string =>
  `${formatCount(value)} ${value === 1 ? singular : plural}`;

export const formatAccidents = (accidents: unknown): string =>
  withNoun(accidents, 'accident', 'accidents');

export const formatLoggedAscents = (ascents: unknown): string =>
  withNoun(ascents, 'logged ascent', 'logged ascents');

export const formatAccidentAscentCounts = (accidents: unknown, ascents: unknown): string =>
  `${formatAccidents(accidents)} · ${formatLoggedAscents(ascents)}`;

interface YearSpan {
  first: number;
  last: number;
}

const isYearSpan = (value: unknown): value is YearSpan => {
  if (typeof value !== 'object' || value === null) return false;
  const { first, last } = value as Record<string, unknown>;
  return Number.isInteger(first) && Number.isInteger(last) && (first as number) <= (last as number);
};

const formatSpan = ({ first, last }: YearSpan): string =>
  (first === last ? `${first}` : `${first}–${last}`);

/**
 * Accidents reach back decades while logged ascents cover a few recent years, so the tab
 * states each side's span. A side with no dated records gets no span; null if neither has one.
 */
export const formatDataSpans = (accidentYears: unknown, ascentYears: unknown): string | null => {
  const parts: string[] = [];
  if (isYearSpan(accidentYears)) parts.push(`Accidents: all recorded years (${formatSpan(accidentYears)}).`);
  if (isYearSpan(ascentYears)) parts.push(`Logged ascents: ${formatSpan(ascentYears)}.`);
  return parts.length ? parts.join(' ') : null;
};
