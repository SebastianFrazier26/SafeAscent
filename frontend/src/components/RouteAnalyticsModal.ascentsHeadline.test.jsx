import { afterEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { cleanup, render, screen } from '../test/utils';
import RouteAnalyticsModal from './RouteAnalyticsModal';
import { SCORE_UNAVAILABLE_MESSAGE } from '../services/api';
import { INSUFFICIENT_DATA_MESSAGE } from '../utils/riskUtils';
import modalSource from './RouteAnalyticsModal.jsx?raw';
import headlineSource from './AscentsHeadline.tsx?raw';
import countsSource from '../utils/ascentCounts.ts?raw';

const ID = 900000001;
const route = {
  route_id: ID, name: 'Synthetic Ice', mountain_name: 'Test Crag', type: 'Ice', grade: 'WI3',
  latitude: 44.1, longitude: -73.9, elevation_meters: null, risk_score: null, color_code: null, mp_route_id: ID,
};
const ascents = {
  has_data: true, total_ascents: 40, total_accidents: 2, peak_month: 'Jan',
  monthly_stats: [{ month: 'Jan', ascent_count: 30, accident_count: 1 }, { month: 'Feb', ascent_count: 10, accident_count: 1 }],
};
const scored = (over = {}) => ({
  status: 'ok',
  data: {
    risk_score: 40, color_code: 'yellow', data_status: 'ok', confidence: 'high', score_range: [20, 60],
    model_version: 'v2-test', evidence: { geo: 3, similar: 5 },
    relative_rate: { mean: 1.46, lo: 0.82, hi: 2.4 },
    per_10k_logged_ascents: { mean: 12.34, lo: 6.1, hi: 30.2 },
    per_10k_reason: null,
    ...over,
  },
});
const insufficient = {
  status: 'insufficient',
  data: { risk_score: null, color_code: 'gray', data_status: 'insufficient_data', insufficient_reason: 'no_evidence', model_version: 'v2-test' },
};
const jsonResponse = (body) => ({ ok: true, json: async () => body, text: async () => '' });

async function openAscents(safety, ascentsBody = ascents) {
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(jsonResponse(url.endsWith('/ascent-analytics') ? ascentsBody : route))));
  const user = userEvent.setup();
  render(
    <RouteAnalyticsModal open onClose={() => {}} selectedDate="2026-01-14" routeData={route}
      safety={safety} onRetrySafety={() => {}} />,
  );
  await user.click(screen.getByRole('tab', { name: 'Ascents' }));
  await screen.findByText(/2 accidents · 40 logged ascents/);
  return document.body;
}

afterEach(() => vi.unstubAllGlobals());

describe('Ascents headline', () => {
  it('renders the relative-rate sentence, confidence and per-10k line from the stored fields', async () => {
    const body = await openAscents(scored());
    expect(body.textContent).toContain('1.5× (0.82–2.4×) the reported-incident rate of ice routes nationally in January');
    expect(body.textContent).toContain('About 12.3 per 10,000 logged ascents (likely 6.1–30.2) · High confidence');
  });

  it('shows the hatch dot and Low confidence label with the number kept', async () => {
    await openAscents(scored({ confidence: 'low' }));
    expect(screen.getByTestId('ascents-headline-hatch')).toBeInTheDocument();
    expect(screen.getByTestId('ascents-headline').textContent).toContain('1.5×');
    expect(screen.getByTestId('ascents-headline').textContent).toContain('Low confidence');
  });

  it('never rounds a small positive value to 0', async () => {
    // relative_rate is a multiplier of the national rate (stored RR), so below-national reads 0.43×, not 0.0043.
    await openAscents(scored({ relative_rate: { mean: 0.432, lo: 0.118, hi: 0.95 }, per_10k_logged_ascents: { mean: 0.0049, lo: 0.0001, hi: 0.4 } }));
    const text = screen.getByTestId('ascents-headline').textContent;
    expect(text).toContain('0.43× (0.12–0.95×)');
    expect(text).toContain('About 0.0049 per 10,000 logged ascents (likely <0.001–0.4)');
  });

  it('renders the unavailable line, never NaN or ∞, for a non-finite or negative rate', async () => {
    for (const bad of [{ mean: NaN, lo: 0.5, hi: 2 }, { mean: 1.2, lo: 0.5, hi: Infinity }, { mean: 1.2, lo: -0.1, hi: 2 }]) {
      await openAscents(scored({ relative_rate: bad }));
      const headline = screen.getByTestId('ascents-headline');
      expect(headline.textContent).toBe('National comparison unavailable');
      expect(document.body.textContent).not.toMatch(/NaN|∞|Infinity/);
      cleanup();
    }
  });

  it('shows the per-10K unavailable sentence, never NaN or ∞, for a non-finite per-10K', async () => {
    await openAscents(scored({ per_10k_logged_ascents: { mean: Infinity, lo: 1, hi: 2 }, per_10k_reason: null }));
    expect(screen.queryByTestId('ascents-per10k')).toBeNull();
    expect(screen.getByTestId('ascents-per10k-reason').textContent).toBe('Logged ascents unavailable');
    expect(screen.getByTestId('ascents-headline').textContent).toContain('1.5×');
    expect(document.body.textContent).not.toMatch(/NaN|∞|Infinity/);
  });

  it('renders one honest line, no numbers and no 0, when relative_rate is null on an ok score', async () => {
    await openAscents(scored({ relative_rate: null }));
    const headline = screen.getByTestId('ascents-headline');
    expect(headline.textContent).toBe('National comparison unavailable');
    expect(screen.queryByTestId('ascents-per10k')).toBeNull();
    expect(screen.queryByTestId('ascents-per10k-reason')).toBeNull();
  });

  it.each([
    ['ticks_incomplete', 'Logged ascents still being collected for this area'],
    ['no_ticks_logged', 'No logged ascents for this route type here'],
    ['ticks_missing', 'Logged ascents unavailable'],
  ])('shows the %s sentence and no per-10k number when per-10k is absent', async (reason, sentence) => {
    await openAscents(scored({ per_10k_logged_ascents: null, per_10k_reason: reason }));
    expect(screen.getByTestId('ascents-per10k-reason').textContent).toBe(sentence);
    expect(screen.queryByTestId('ascents-per10k')).toBeNull();
    expect(screen.getByTestId('ascents-headline').textContent).not.toMatch(/per 10,000/);
  });

  it('shows no per-10k line at all when per-10k and its reason are both absent', async () => {
    await openAscents(scored({ per_10k_logged_ascents: null, per_10k_reason: null }));
    expect(screen.getByTestId('ascents-headline').textContent).not.toMatch(/per 10,000|Logged ascents/);
  });

  it('an insufficient score shows the gray chip message and no number or headline', async () => {
    await openAscents(insufficient);
    const headline = screen.getByTestId('ascents-headline');
    expect(headline.textContent).toBe(INSUFFICIENT_DATA_MESSAGE);
    expect(headline.textContent).not.toMatch(/\d|×|per 10,000/);
  });

  it('an unavailable score shows the unavailable state, never the headline', async () => {
    await openAscents({ status: 'error', message: SCORE_UNAVAILABLE_MESSAGE, unavailable: true });
    const headline = screen.getByTestId('ascents-headline');
    expect(headline.textContent).toContain(SCORE_UNAVAILABLE_MESSAGE);
    expect(headline.textContent).not.toMatch(/\d|×|per 10,000|nationally/);
  });

  it('computes no accident-per-ascent rate from the counts for any fixture (F65)', async () => {
    const fixtures = [
      scored({ per_10k_logged_ascents: null, per_10k_reason: 'ticks_incomplete' }),
      scored({ relative_rate: null }),
      insufficient,
    ];
    for (const safety of fixtures) {
      await openAscents(safety);
      const text = document.body.textContent;
      expect(text).toContain('2 accidents · 40 logged ascents');
      // 2 accidents / 40 ascents = 0.05 = 5% = 1 in 20 = 500 per 10,000
      expect(text).not.toMatch(/\b500\b|0\.05\b|\b5(\.0)?\s?%|1 in 20/);
      cleanup();
    }
  });

  it('names no accident-over-tick ratio in the Ascents sources', () => {
    const pattern = /(accident|tick)\w*\s*\/\s*\w*(accident|tick)/i;
    for (const source of [modalSource, headlineSource, countsSource]) {
      expect(source).not.toMatch(pattern);
    }
  });
});
