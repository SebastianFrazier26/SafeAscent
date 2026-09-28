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

/** Routes with linked accidents but no logged ascents must still show the accidents. */
export const formatAccidentsWithoutAscents = (accidents: unknown): string =>
  `${formatAccidents(accidents)} · no logged ascents yet`;

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
 * Totals include undated records but no month or year span can, so each place that shows
 * months or spans says how many were left out. Null when there are none (or the count is bad).
 */
export const formatUndated = (count: unknown): string | null =>
  (isCount(count) && count > 0 ? `(+${formatCount(count)} undated)` : null);

/**
 * Accidents reach back decades while logged ascents cover a few recent years, so the tab
 * states each side's span. A side with neither a span nor undated records is dropped;
 * null if both are.
 */
export const formatDataSpans = (
  accidentYears: unknown,
  ascentYears: unknown,
  undatedAccidents?: unknown,
  undatedAscents?: unknown,
): string | null => {
  const side = (span: unknown, undated: unknown, dated: (s: string) => string, name: string) => {
    const extra = formatUndated(undated);
    if (isYearSpan(span)) return `${dated(formatSpan(span))}${extra ? ` ${extra}` : ''}.`;
    return extra ? `${name}: none dated ${extra}.` : null;
  };
  const parts = [
    side(accidentYears, undatedAccidents, (s) => `Accidents: all recorded years (${s})`, 'Accidents'),
    side(ascentYears, undatedAscents, (s) => `Logged ascents: ${s}`, 'Logged ascents'),
  ].filter((part): part is string => part !== null);
  return parts.length ? parts.join(' ') : null;
};
