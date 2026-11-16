/**
 * One decimal from 1 up, two significant figures below 1, so a small positive value never reads as 0.
 * Anything that is not a finite non-negative number is null: the caller shows its unavailable line,
 * never "NaN" or "∞".
 */
export const formatSmallSafe = (value: unknown): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null;
  if (value === 0) return '0';
  if (value < 0.001) return '<0.001';
  if (value < 1) {
    const twoSig = Number(value.toPrecision(2));
    // 0.999 rounds up to 1: fall through so it reads "1.0" like every other value from 1 up.
    if (twoSig < 1) return String(twoSig);
  }
  // Decide the format on the shown value, so 99.96 (shown 100.0) reads "100" like 100 itself.
  if (Number(value.toFixed(1)) >= 100) return Math.round(value).toLocaleString('en-US');
  return value.toFixed(1);
};
