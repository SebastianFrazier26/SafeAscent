import { describe, expect, it } from 'vitest';
import { formatSmallSafe } from './formatSmallSafe';

describe('formatSmallSafe', () => {
  it.each([
    [0, '0'],
    [0.0009, '<0.001'],
    [0.001, '0.001'],
    [0.0432, '0.043'],
    [0.999, '1.0'],
    [1, '1.0'],
    [1.46, '1.5'],
    [99.94, '99.9'],
    [99.96, '100'],
    [1234, '1,234'],
  ])('formats %s as %s', (value, expected) => {
    expect(formatSmallSafe(value)).toBe(expected);
  });

  it.each([NaN, Infinity, -Infinity, -0.5, '1.2', null, undefined])('refuses %s', (value) => {
    expect(formatSmallSafe(value)).toBeNull();
  });
});
