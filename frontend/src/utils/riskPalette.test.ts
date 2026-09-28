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

describe('no fabricated risk score', () => {
  // `risk_score || 0` turned a failed request into "Risk 0.0" (a green "safe" route).
  const scoreFallback = /risk\w*[^\n]*(\|\||\?\?)\s*0\b/i;
  const appSources = import.meta.glob('../App.jsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
  const files: Record<string, string> = {
    'MapView.jsx': source('MapView.jsx'),
    'RouteAnalyticsModal.jsx': source('RouteAnalyticsModal.jsx'),
    'PredictionResult.jsx': source('PredictionResult.jsx'),
    'App.jsx': appSources['../App.jsx'] ?? '',
  };

  it.each(Object.keys(files))('%s never falls a risk value back to 0', (name) => {
    expect(files[name]).not.toBe('');
    const hits = files[name].split('\n').filter((line) => scoreFallback.test(line));
    expect(hits).toEqual([]);
  });

  it('catches the old patterns and spares the cluster coalesce', () => {
    expect(scoreFallback.test('risk_score: safetyData.risk_score || 0,')).toBe(true);
    expect(scoreFallback.test('const s = route.risk_score ?? 0;')).toBe(true);
    expect(scoreFallback.test('{data.summary?.avg_risk || 0}/100')).toBe(true);
    expect(scoreFallback.test('const riskScore = prediction.riskScore ?? 0;')).toBe(true);
    expect(scoreFallback.test("risk_score_sum: ['+', ['coalesce', ['get', 'risk_score'], 0]],")).toBe(false);
    expect(source('MapView.jsx')).toContain("['coalesce', ['get', 'risk_score'], 0]");
  });
});

describe('MapView heatmap ramps', () => {
  it('derive band colours from the palette instead of rgba literals', () => {
    const text = source('MapView.jsx').replace(/\s+/g, '');
    for (const hex of [...Object.values(RISK_COLOR_HEX), NO_RISK_HEX]) {
      const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(',');
      expect(text).not.toContain(`rgba(${rgb},`);
    }
    expect(text).toContain('hexToRgba(hex,alpha)');
    expect(text.match(/'heatmap-color':HEATMAP_COLOR\./g)).toHaveLength(5);
  });
});
