import { describe, expect, it } from 'vitest';
import { contrastRatio, hexToRgba, mixHex, readableTextOn } from './color';
import { NO_RISK_HEX, RISK_COLOR_HEX, RISK_TEXT_ON_HEX } from './riskUtils';

describe('contrastRatio', () => {
  it('matches the WCAG reference values', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#ffffff', '#ffffff')).toBeCloseTo(1, 5);
    expect(contrastRatio('#777777', '#ffffff')).toBeCloseTo(4.48, 2);
  });
});

describe('readableTextOn', () => {
  it.each([...Object.entries(RISK_COLOR_HEX), ['none', NO_RISK_HEX]])(
    'text on %s (%s) reaches 4.5:1',
    (_name, hex) => {
      expect(contrastRatio(readableTextOn(hex), hex)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it('is what the risk chips use', () => {
    for (const [code, hex] of Object.entries(RISK_COLOR_HEX)) {
      expect(RISK_TEXT_ON_HEX[code as keyof typeof RISK_COLOR_HEX]).toBe(readableTextOn(hex));
    }
  });

  it('picks white on dark backgrounds', () => {
    expect(readableTextOn('#0d47a1')).toBe('#ffffff');
  });
});

describe('hexToRgba', () => {
  it('converts a palette hex with alpha', () => {
    expect(hexToRgba('#4caf50', 0.4)).toBe('rgba(76, 175, 80, 0.4)');
    expect(hexToRgba(NO_RISK_HEX, 0)).toBe('rgba(158, 158, 158, 0)');
  });
});

describe('mixHex', () => {
  it('interpolates between two colours', () => {
    expect(mixHex('#000000', '#ffffff', 0)).toBe('#000000');
    expect(mixHex('#000000', '#ffffff', 1)).toBe('#ffffff');
    expect(mixHex('#000000', '#ffffff', 0.5)).toBe('#808080');
  });
});
