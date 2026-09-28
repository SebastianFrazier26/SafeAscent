import { describe, expect, it } from 'vitest';
import { contrastRatio } from './color';
import {
  LEGEND_PANEL_BG,
  LEGEND_SWATCHES,
  NO_RISK_HEX,
  RISK_COLOR_HEX,
  legendSwatchBorder,
  routeSafetyProps,
} from './riskUtils';

describe('routeSafetyProps (map feature properties from the bulk safety payload)', () => {
  it('keeps a real score and re-derives its band colour', () => {
    expect(routeSafetyProps({ risk_score: 27, color_code: 'green', data_status: 'ok' })).toEqual({
      risk_score: 27,
      color_code: 'yellow',
      data_status: 'ok',
    });
  });

  it('marks insufficient data gray with no score, so clusters leave it out of averages', () => {
    expect(routeSafetyProps({ risk_score: null, color_code: 'gray', data_status: 'insufficient_data' })).toEqual({
      risk_score: null,
      color_code: 'gray',
      data_status: 'insufficient_data',
    });
  });

  it.each([
    ['missing', null],
    ['gray with a number', { risk_score: 12, color_code: 'gray', data_status: 'insufficient_data' }],
    ['ok with null', { risk_score: null, color_code: 'gray', data_status: 'ok' }],
    ['out of range', { risk_score: 140, color_code: 'red', data_status: 'ok' }],
  ])('treats %s as unscored gray, never 0', (_label, safety) => {
    expect(routeSafetyProps(safety)).toEqual({ risk_score: null, color_code: 'gray', data_status: null });
  });
});

describe('legend swatches', () => {
  it('keep the palette fills and include an Insufficient data gray swatch', () => {
    const fills = LEGEND_SWATCHES.map((s) => s.fill);
    expect(fills).toEqual([...Object.values(RISK_COLOR_HEX), NO_RISK_HEX]);
    expect(LEGEND_SWATCHES[LEGEND_SWATCHES.length - 1]?.label).toBe('Insufficient data');
  });

  it.each([
    ['the legend panel', LEGEND_PANEL_BG],
    ['a white panel', '#ffffff'],
  ])('each swatch border reaches 3:1 against %s', (_label, panel) => {
    for (const { fill } of LEGEND_SWATCHES) {
      expect(contrastRatio(legendSwatchBorder(fill, panel), panel)).toBeGreaterThanOrEqual(3);
    }
  });

  it('darkens the yellow border on a white panel instead of changing its fill', () => {
    const border = legendSwatchBorder(RISK_COLOR_HEX.yellow, '#ffffff');
    expect(border).not.toBe(RISK_COLOR_HEX.yellow);
    expect(contrastRatio(RISK_COLOR_HEX.yellow, '#ffffff')).toBeLessThan(3);
  });
});
