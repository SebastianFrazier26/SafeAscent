import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { act, render, screen, within } from '../test/utils';
import RouteAnalyticsModal from './RouteAnalyticsModal';
import { NO_RISK_HEX } from '../utils/riskUtils';
import { readableTextOn } from '../utils/color';

const route = (id, name) => ({
  route_id: id,
  name,
  mountain_name: 'Test Crag',
  type: 'Ice',
  grade: 'WI3',
  latitude: 44.1,
  longitude: -73.9,
  elevation_meters: null,
  risk_score: null,
  color_code: null,
  mp_route_id: id,
});

const LOADING_SAFETY = { status: 'loading' };

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

const jsonResponse = (body) => ({ ok: true, json: async () => body, text: async () => '' });

function Modal({ routeData, selectedDate = '2026-09-27' }) {
  return (
    <RouteAnalyticsModal
      open
      onClose={() => {}}
      selectedDate={selectedDate}
      routeData={routeData}
      safety={LOADING_SAFETY}
      onRetrySafety={() => {}}
    />
  );
}

afterEach(() => vi.unstubAllGlobals());

describe('RouteAnalyticsModal tab data belongs to the current route', () => {
  let pending;

  beforeEach(() => {
    pending = new Map();
    vi.stubGlobal('fetch', vi.fn((url) => {
      const d = deferred();
      pending.set(url, d);
      return d.promise;
    }));
  });

  const pendingFor = (fragment) => {
    const hit = [...pending.entries()].find(([url]) => url.endsWith(fragment));
    if (!hit) throw new Error(`no request for ${fragment}`);
    return hit[1];
  };

  it("drops route A's late response after switching to route B", async () => {
    const { rerender } = render(<Modal routeData={route(1, 'Route A')} />);
    const a = pendingFor('/mp-routes/1');

    rerender(<Modal routeData={route(2, 'Route B')} />);
    const b = pendingFor('/mp-routes/2');

    await act(async () => b.resolve(jsonResponse({ ...route(2, 'Bravo Details'), location_name: 'B Valley' })));
    await act(async () => a.resolve(jsonResponse({ ...route(1, 'Alpha Details'), location_name: 'A Valley' })));

    expect(screen.getByText(/Bravo Details/)).toBeInTheDocument();
    expect(screen.queryByText(/Alpha Details/)).toBeNull();
    expect(screen.queryByText(/A Valley/)).toBeNull();
  });

  it("never shows route A's response when it lands before route B's", async () => {
    const { rerender } = render(<Modal routeData={route(1, 'Route A')} />);
    const a = pendingFor('/mp-routes/1');

    rerender(<Modal routeData={route(2, 'Route B')} />);
    await act(async () => a.resolve(jsonResponse({ ...route(1, 'Alpha Details'), location_name: 'A Valley' })));

    expect(screen.queryByText(/Alpha Details/)).toBeNull();
    expect(screen.queryByText(/A Valley/)).toBeNull();
  });

  it('does not refetch when the same route arrives as a new object (safety loaded)', () => {
    const { rerender } = render(<Modal routeData={route(1, 'Route A')} />);
    rerender(<Modal routeData={{ ...route(1, 'Route A'), risk_score: 42.3, color_code: 'yellow' }} />);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe('RouteAnalyticsModal missing hourly scores', () => {
  const timeOfDay = {
    hourly_data: [
      { hour: 6, risk_score: undefined, conditions_summary: 'Clear', temperature: 2, wind_speed: 3, precipitation: 0, is_climbable: true },
      { hour: 7, risk_score: null, conditions_summary: 'Clear', temperature: 3, wind_speed: 3, precipitation: 0, is_climbable: true },
    ],
    best_window: { start_hour: 6, end_hour: 9, duration_hours: 3, avg_risk: undefined, conditions: 'Clear' },
    climbing_windows: [
      { start_hour: 6, end_hour: 9, duration_hours: 3, avg_risk: null, conditions: 'Clear' },
    ],
  };

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((url) =>
      Promise.resolve(url.includes('/time-of-day') ? jsonResponse(timeOfDay) : jsonResponse(route(1, 'Route A'))),
    ));
  });

  it('shows Unavailable / an em-dash in neutral grey, never undefined, NaN or a number', async () => {
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Time of Day' }));
    await screen.findByText(/Hourly Conditions Detail/);

    const panel = screen.getByRole('dialog');
    expect(within(panel).queryByText(/undefined|NaN/)).toBeNull();

    const neutral = { backgroundColor: NO_RISK_HEX, color: readableTextOn(NO_RISK_HEX) };
    const hourChips = within(panel).getAllByText('—');
    expect(hourChips).toHaveLength(2);
    for (const chip of hourChips) expect(chip.closest('.MuiChip-root')).toHaveStyle(neutral);

    const unavailable = within(panel).getAllByText('Unavailable');
    // best-window "Avg Risk" figure + the climbing-window chip
    expect(unavailable).toHaveLength(2);
    const windowChip = unavailable.map((el) => el.closest('.MuiChip-root')).find(Boolean);
    expect(windowChip).toHaveStyle(neutral);
  });
});
