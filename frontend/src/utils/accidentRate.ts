/**
 * Ascent-analytics accident rates are accidents per 1,000 recorded ascents
 * (backend/app/api/v1/mp_routes.py get_ascent_analytics), not a percentage.
 */

export const ACCIDENT_RATE_UNIT = 'per 1,000 ascents';

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

/**
 * The number alone, for places that print the unit separately. The backend sends 0.0 when
 * there are no ascents, so a zero ascent count must never read as a "0" rate.
 */
export const formatAccidentRateValue = (rate: unknown, ascents?: unknown): string => {
  if (isFiniteNumber(ascents) && ascents <= 0) return 'No ascents';
  if (!isFiniteNumber(rate)) return 'Unavailable';
  return rate.toLocaleString('en-US', { maximumFractionDigits: 2 });
};

export const hasAccidentRate = (rate: unknown, ascents?: unknown): boolean =>
  isFiniteNumber(rate) && !(isFiniteNumber(ascents) && ascents <= 0);

export const formatAccidentRate = (rate: unknown, ascents?: unknown): string => {
  const value = formatAccidentRateValue(rate, ascents);
  return hasAccidentRate(rate, ascents) ? `${value} ${ACCIDENT_RATE_UNIT}` : value;
};
