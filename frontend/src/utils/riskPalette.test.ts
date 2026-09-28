import { describe, expect, it } from 'vitest';
import {
  NO_RISK_HEX,
  RISK_COLOR_HEX,
  RISK_TEXT_ON_HEX,
  getMarkerColor,
  getRiskColorCode,
} from './riskUtils';

const sources = import.meta.glob('../components/*.jsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const source = (name: string): string => {
  const text = sources[`../components/${name}`];
  if (text === undefined) throw new Error(`missing component source ${name}`);
  return text;
};

describe('shared risk palette', () => {
  it('gives each band exactly one distinct hex', () => {
    expect(Object.keys(RISK_COLOR_HEX).sort()).toEqual(['green', 'orange', 'red', 'yellow']);
    const hexes = Object.values(RISK_COLOR_HEX);
    for (const hex of hexes) expect(hex).toMatch(/^#[0-9a-f]{6}$/);
    expect(new Set([...hexes, NO_RISK_HEX]).size).toBe(5);
  });

  it('marker colour is the palette hex for the score band', () => {
    for (const score of [0, 24.99, 25, 50, 75, 100]) {
      expect(getMarkerColor(score)).toBe(RISK_COLOR_HEX[getRiskColorCode(score)]);
    }
  });

  it('has a text colour for every band', () => {
    expect(Object.keys(RISK_TEXT_ON_HEX).sort()).toEqual(Object.keys(RISK_COLOR_HEX).sort());
  });
});

describe('components use the shared palette, not their own', () => {
  const bandHexes = [...Object.values(RISK_COLOR_HEX), NO_RISK_HEX];
  // A band-keyed colour table (`green: '#...'`, mapbox `'green', '#...'`) is a second palette.
  const bandKeyedPalette = /['"]?\b(green|yellow|orange|red)\b['"]?\s*[:,]\s*['"](#|[a-z]+\.(main|dark|light))/i;

  it.each(['MapView.jsx', 'RouteAnalyticsModal.jsx', 'PredictionResult.jsx'])(
    '%s imports RISK_COLOR_HEX and defines no band-keyed palette of its own',
    (name) => {
      const text = source(name);
      expect(text).toMatch(/import\s*{[^}]*\bRISK_COLOR_HEX\b[^}]*}\s*from\s*'\.\.\/utils\/riskUtils'/);
      expect(text).not.toMatch(bandKeyedPalette);
    },
  );

  // The modal reuses some of these hexes for unrelated things (route-type badges, chart
  // series), so the literal-hex ban is MapView-only, where every one of them was a band.
  it('MapView hardcodes no band hex', () => {
    const text = source('MapView.jsx').toLowerCase();
    for (const hex of bandHexes) expect(text).not.toContain(hex);
  });

  it('MapView fetches the score through useRouteSafety, not a raw fetch', () => {
    const text = source('MapView.jsx');
    expect(text).toContain('useRouteSafety(');
    expect(text).not.toMatch(/\/safety\?/);
    expect(text).not.toMatch(/safetyData/);
  });
});
