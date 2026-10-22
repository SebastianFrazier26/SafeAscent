import { describe, expect, it } from 'vitest';
import { contrastRatio } from './color';
import {
  INSUFFICIENT_DATA_LABEL,
  INSUFFICIENT_DATA_MESSAGE,
  UNAVAILABLE_SAFETY,
  hatchImage,
  LEGEND_PANEL_BG,
  LEGEND_SWATCHES,
  NO_RISK_HEX,
  RISK_COLOR_HEX,
  formatRiskTooltip,
  legendSwatchBorder,
  routeSafetyProps,
} from './riskUtils';

describe('insufficient-data wording', () => {
  it('names what is missing, and never says "no evidence" or that the route is safe', () => {
    expect(INSUFFICIENT_DATA_MESSAGE).toBe('Not enough reported incidents or similar routes to estimate');
    expect(INSUFFICIENT_DATA_LABEL).toBe('Not enough evidence');
    const gray = LEGEND_SWATCHES.find((s) => s.fill === NO_RISK_HEX);
    expect(gray?.caption).toBe(INSUFFICIENT_DATA_MESSAGE);
    expect(gray?.caption).not.toMatch(/no .*evidence|safe/i);
  });

  it('chart tooltips name insufficient points and give 1 decimal otherwise', () => {
    expect(formatRiskTooltip(null, 'Risk Score', { payload: { data_status: 'insufficient_data' } })).toEqual([
      'Not enough evidence',
      'Risk Score',
    ]);
    expect(formatRiskTooltip(30, 'Risk Score', { payload: { data_status: 'ok' } })).toEqual(['30.0/100', 'Risk Score']);
    expect(formatRiskTooltip(undefined, 'Risk Score', {})).toEqual(['Unavailable', 'Risk Score']);
  });
});

const STORED_OK = {
  risk_score: 40,
  color_code: 'yellow',
  data_status: 'ok',
  confidence: 'low',
  score_range: [20, 60],
  model_version: 'v2',
  evidence: { geo: 1, similar: 3 },
};

describe('routeSafetyProps (map feature properties from the bulk safety payload)', () => {
  it('keeps a real score, re-derives its band colour and carries its confidence', () => {
    expect(routeSafetyProps({ risk_score: 27, color_code: 'green', data_status: 'ok' })).toEqual({
      risk_score: 27,
      color_code: 'yellow',
      data_status: 'ok',
      confidence: null,
      hatch: false,
    });
  });

  it('hatches a low-confidence stored score', () => {
    expect(routeSafetyProps(STORED_OK)).toEqual({
      risk_score: 40,
      color_code: 'yellow',
      data_status: 'ok',
      confidence: 'low',
      hatch: true,
    });
    expect(routeSafetyProps({ ...STORED_OK, confidence: 'high' }).hatch).toBe(false);
  });

  it('marks insufficient data gray and hatched with no score, so clusters leave it out of averages', () => {
    expect(routeSafetyProps({ risk_score: null, color_code: 'gray', data_status: 'insufficient_data' })).toEqual({
      risk_score: null,
      color_code: 'gray',
      data_status: 'insufficient_data',
      confidence: null,
      hatch: true,
    });
  });

  it('maps a route with no stored row (safety: null) to unavailable: no colour, never gray, never 0', () => {
    expect(routeSafetyProps(null)).toEqual(UNAVAILABLE_SAFETY);
    expect(UNAVAILABLE_SAFETY).toEqual({
      risk_score: null,
      color_code: null,
      data_status: null,
      confidence: null,
      hatch: false,
    });
  });

  it.each([
    ['undefined', undefined],
    ['gray with a number', { risk_score: 12, color_code: 'gray', data_status: 'insufficient_data' }],
    ['ok with null', { risk_score: null, color_code: 'gray', data_status: 'ok' }],
    ['out of range', { risk_score: 140, color_code: 'red', data_status: 'ok' }],
  ])('treats %s as unavailable, not gray and not 0', (_label, safety) => {
    expect(routeSafetyProps(safety)).toEqual(UNAVAILABLE_SAFETY);
  });
});

describe('legend swatches', () => {
  it('keep the band fills, then the hatch, no-evidence gray and unavailable swatches', () => {
    expect(LEGEND_SWATCHES.map((s) => s.fill).slice(0, 4)).toEqual(Object.values(RISK_COLOR_HEX));
    const byLabel = Object.fromEntries(LEGEND_SWATCHES.map((s) => [s.label, s]));
    expect(byLabel['Low confidence']?.hatch).toBe(true);
    expect(byLabel['Not enough evidence']).toMatchObject({ fill: NO_RISK_HEX, hatch: true });
    expect(byLabel['Unavailable']).toMatchObject({ fill: null, hatch: false });
  });

  it('never call a band safe or Low Risk', () => {
    for (const { label, caption } of LEGEND_SWATCHES) {
      expect(`${label} ${caption}`).not.toMatch(/\bsafe\b|low risk|favorable/i);
    }
    expect(LEGEND_SWATCHES[0]?.caption).toBe('Lower than typical for this type');
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

describe('hatch overlay image', () => {
  it('is a transparent-cornered circle of diagonal stripes', () => {
    const img = hatchImage(24);
    expect(img.width).toBe(24);
    expect(img.data).toHaveLength(24 * 24 * 4);
    const alpha = (x: number, y: number) => img.data[(y * 24 + x) * 4 + 3];
    expect(alpha(0, 0)).toBe(0);
    const inside = [];
    for (let x = 6; x < 18; x += 1) inside.push(alpha(x, 12));
    expect(inside.some((a) => a > 0)).toBe(true);
    expect(inside.some((a) => a === 0)).toBe(true);
  });
});
