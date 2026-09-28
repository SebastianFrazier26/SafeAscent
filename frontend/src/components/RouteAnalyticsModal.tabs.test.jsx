import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { act, render, screen, within } from '../test/utils';
import RouteAnalyticsModal from './RouteAnalyticsModal';
import { INSUFFICIENT_DATA_MESSAGE, NO_RISK_HEX } from '../utils/riskUtils';
import { contrastRatio, readableTextOn } from '../utils/color';

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
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
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
  let requests;

  beforeEach(() => {
    pending = new Map();
    requests = [];
    vi.stubGlobal('fetch', vi.fn((url) => {
      const d = deferred();
      pending.set(url, d);
      requests.push({ url, ...d });
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

  it("drops route A's late rejection after switching to route B", async () => {
    const { rerender } = render(<Modal routeData={route(1, 'Route A')} />);
    const a = pendingFor('/mp-routes/1');

    rerender(<Modal routeData={route(2, 'Route B')} />);
    const b = pendingFor('/mp-routes/2');
    await act(async () => a.reject(new Error('Route A exploded')));
    await act(async () => b.resolve(jsonResponse({ ...route(2, 'Bravo Details') })));

    expect(screen.queryByText(/Route A exploded/)).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByText(/Bravo Details/)).toBeInTheDocument();
  });

  it("drops the old date's response after the date changes", async () => {
    const { rerender } = render(<Modal routeData={route(1, 'Route A')} selectedDate="2026-09-27" />);
    rerender(<Modal routeData={route(1, 'Route A')} selectedDate="2026-09-28" />);
    expect(requests).toHaveLength(2);

    await act(async () => requests[1].resolve(jsonResponse({ ...route(1, 'Details for 28th') })));
    await act(async () => requests[0].resolve(jsonResponse({ ...route(1, 'Details for 27th') })));

    expect(screen.getByText(/Details for 28th/)).toBeInTheDocument();
    expect(screen.queryByText(/Details for 27th/)).toBeNull();
  });

  it('A -> B -> A shows only the fresh response for A', async () => {
    const { rerender } = render(<Modal routeData={route(1, 'Route A')} />);
    rerender(<Modal routeData={route(2, 'Route B')} />);
    rerender(<Modal routeData={route(1, 'Route A')} />);
    expect(requests.map((r) => r.url.split('/').pop())).toEqual(['1', '2', '1']);

    await act(async () => requests[2].resolve(jsonResponse({ ...route(1, 'Alpha Fresh') })));
    await act(async () => requests[0].resolve(jsonResponse({ ...route(1, 'Alpha Stale') })));
    await act(async () => requests[1].resolve(jsonResponse({ ...route(2, 'Bravo Details') })));

    expect(screen.getByText(/Alpha Fresh/)).toBeInTheDocument();
    expect(screen.queryByText(/Alpha Stale/)).toBeNull();
    expect(screen.queryByText(/Bravo Details/)).toBeNull();
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

describe('RouteAnalyticsModal accident and ascent figures', () => {
  const accidents = {
    location_name: 'Test Crag',
    accidents: [
      { accident_id: 1, date: '2020-01-01', injury_severity: 'Fatal', same_route: false },
      { accident_id: 2, date: '2020-01-02', injury_severity: 'Serious', same_route: false },
      { accident_id: 3, date: '2020-01-03', injury_severity: 'Moderate', same_route: false },
      { accident_id: 4, date: '2020-01-04', injury_severity: 'Minor', same_route: false },
      { accident_id: 5, date: '2020-01-05', injury_severity: 'Unspecified', same_route: false },
    ],
  };
  const ascents = {
    has_data: true,
    total_ascents: 40,
    total_accidents: 2,
    peak_month: 'Jan',
    accident_years: { first: 1998, last: 2021 },
    ascent_years: { first: 2024, last: 2026 },
    monthly_stats: [
      { month: 'Jan', ascent_count: 30, accident_count: 0 },
      { month: 'Feb', ascent_count: 10, accident_count: undefined },
      { month: 'Mar', ascent_count: 1, accident_count: 1 },
      { month: 'Apr', ascent_count: 0, accident_count: 1 },
    ],
  };

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/accidents')) return Promise.resolve(jsonResponse(accidents));
      if (url.endsWith('/ascent-analytics')) return Promise.resolve(jsonResponse(ascents));
      return Promise.resolve(jsonResponse(route(1, 'Route A')));
    }));
  });

  const expectReadable = (el) => {
    const chip = el.closest('.MuiChip-root');
    const { backgroundColor, color } = getComputedStyle(chip);
    const hex = (rgb) => `#${rgb.match(/\d+/g).slice(0, 3).map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
    expect(contrastRatio(hex(color), hex(backgroundColor))).toBeGreaterThanOrEqual(4.5);
  };

  it('severity chips have readable text', async () => {
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Accident Reports' }));
    for (const label of ['Fatal', 'Serious', 'Moderate', 'Minor', 'Unspecified']) {
      expectReadable(await screen.findByText(label));
    }
  });

  it('an FK-linked accident with no coordinates is listed, flagged and exported', async () => {
    const withUnlocated = {
      ...accidents,
      accidents: [
        {
          accident_id: 9, date: '2018-05-01', route_name: 'Route A', injury_severity: 'Serious',
          same_route: true, distance_km: null, impact_score: null, coordinates: null,
        },
        ...accidents.accidents,
      ],
    };
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/accidents')) return Promise.resolve(jsonResponse(withUnlocated));
      if (url.endsWith('/ascent-analytics')) return Promise.resolve(jsonResponse(ascents));
      return Promise.resolve(jsonResponse(route(1, 'Route A')));
    }));
    const parts = [];
    vi.stubGlobal('Blob', class {
      constructor(content) { parts.push(content.join('')); }
    });
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: () => {} });
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Accident Reports' }));
    await screen.findByText('SAME ROUTE');

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Showing 6 of 6 accidents.', { exact: false })).toBeInTheDocument();
    expect(within(dialog).getAllByText('Distance unknown')).toHaveLength(6);
    expect(within(dialog).queryByText(/NaN|undefined|null km/)).toBeNull();

    await user.click(screen.getByTitle('Export Analytics Data'));
    await user.click(await screen.findByText(/Export as CSV/));
    expect(parts).toHaveLength(1);
    expect(parts[0]).toContain('2018-05-01,Route A,Yes,Serious,');
  });

  it('shows plain counts in neutral chips, never a rate or a green zero', async () => {
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Ascents' }));
    await screen.findByText(/Counts by Month/);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('2 accidents · 40 logged ascents')).toBeInTheDocument();
    expect(within(dialog).getByText(/logged ascents, which undercount real ascents/)).toBeInTheDocument();
    expect(within(dialog).getByText('Accidents: all recorded years (1998–2021). Logged ascents: 2024–2026.'))
      .toBeInTheDocument();
    expect(within(dialog).queryByText(/undefined|NaN/)).toBeNull();
    expect(within(dialog).queryByText(/\d%|per 1,000|per 10,000|Accident Rate|Safest|Highest Risk/i)).toBeNull();

    const labels = [
      '0 accidents · 30 logged ascents',
      '— accidents · 10 logged ascents',
      '1 accident · 1 logged ascent',
      '1 accident · 0 logged ascents',
    ];
    const chips = labels.map((label) => within(dialog).getByText(label).closest('.MuiChip-root'));
    for (const chip of chips) {
      expect(chip).toHaveClass('MuiChip-outlined');
      expect(chip).not.toHaveClass('MuiChip-colorSuccess');
      expect(chip).not.toHaveClass('MuiChip-colorError');
    }
  });

  it('a side with no dated records gets no span', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/ascent-analytics')) {
        return Promise.resolve(jsonResponse({ ...ascents, accident_years: null }));
      }
      if (url.endsWith('/accidents')) return Promise.resolve(jsonResponse(accidents));
      return Promise.resolve(jsonResponse(route(1, 'Route A')));
    }));
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Ascents' }));
    await screen.findByText(/Counts by Month/);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Logged ascents: 2024–2026.')).toBeInTheDocument();
    expect(within(dialog).queryByText(/all recorded years/)).toBeNull();
  });

  const stubFetch = ({ accidentsBody = accidents, ascentsBody = ascents } = {}) => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/ascent-analytics')) return Promise.resolve(jsonResponse(ascentsBody));
      if (url.endsWith('/accidents')) return Promise.resolve(jsonResponse(accidentsBody));
      return Promise.resolve(jsonResponse(route(1, 'Route A')));
    }));
  };

  it('a route without coordinates says nearby accidents are unavailable, not that there are none', async () => {
    stubFetch({ accidentsBody: { location_name: 'Unknown Area', accidents: [], total_accidents: 0, nearby_search: false } });
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Accident Reports' }));

    expect(await screen.findByText("Nearby accidents unavailable: this route's area has no coordinates."))
      .toBeInTheDocument();
    expect(screen.queryByText(/No accident reports found/)).toBeNull();
  });

  it('linked reports on a route without coordinates are listed under the unavailable notice', async () => {
    stubFetch({
      accidentsBody: {
        location_name: 'Unknown Area',
        total_accidents: 1,
        nearby_search: false,
        accidents: [{ accident_id: 9, date: '2018-05-01', injury_severity: 'Serious', same_route: true, distance_km: null }],
      },
    });
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Accident Reports' }));
    await screen.findByText('SAME ROUTE');

    expect(screen.getByText("Nearby accidents unavailable: this route's area has no coordinates.")).toBeInTheDocument();
  });

  it('"Showing X of Y" uses the true total, not the returned page', async () => {
    stubFetch({ accidentsBody: { ...accidents, total_accidents: 120, nearby_search: true } });
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Accident Reports' }));

    expect(await screen.findByText('Showing 5 of 120 accidents.', { exact: false })).toBeInTheDocument();
  });

  it('a route with accidents but no logged ascents still shows the accident count', async () => {
    stubFetch({
      ascentsBody: {
        ...ascents,
        has_data: false,
        total_ascents: 0,
        total_accidents: 3,
        ascent_years: null,
        peak_month: null,
        message: 'No tick data available yet for this route.',
      },
    });
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Ascents' }));

    expect(await screen.findByText('3 accidents · no logged ascents yet')).toBeInTheDocument();
    expect(screen.queryByText('No tick data available yet for this route.')).toBeNull();
  });

  it('undated records are named next to the spans and the month breakdown', async () => {
    stubFetch({ ascentsBody: { ...ascents, undated_accidents: 1, undated_ascents: 2 } });
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Ascents' }));
    await screen.findByText(/Counts by Month/);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(
      'Accidents: all recorded years (1998–2021) (+1 undated). Logged ascents: 2024–2026 (+2 undated).',
    )).toBeInTheDocument();
    expect(within(dialog).getByText('Not in any month: accidents (+1 undated), logged ascents (+2 undated).'))
      .toBeInTheDocument();
    expect(within(dialog).getByText('Logged ascents by month (+2 undated)')).toBeInTheDocument();
    expect(within(dialog).getByText('Accidents by month (+1 undated)')).toBeInTheDocument();
  });

  it('a route with no logged ascents shows the no-data state, not zero counts', async () => {
    const noAscents = {
      ...ascents,
      has_data: false,
      total_ascents: 0,
      total_accidents: 0,
      peak_month: null,
      message: 'No tick data available yet for this route.',
      monthly_stats: ascents.monthly_stats.map((m) => ({ ...m, ascent_count: 0, accident_count: 0 })),
    };
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/ascent-analytics')) return Promise.resolve(jsonResponse(noAscents));
      if (url.endsWith('/accidents')) return Promise.resolve(jsonResponse(accidents));
      return Promise.resolve(jsonResponse(route(1, 'Route A')));
    }));
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Ascents' }));
    await screen.findByText('No tick data available yet for this route.');

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).queryByText(/Counts by Month|0 accidents/)).toBeNull();
  });
});

describe('RouteAnalyticsModal insufficient-data days and hours', () => {
  const insufficientDay = (date) => ({
    date,
    risk_score: null,
    color_code: 'gray',
    data_status: 'insufficient_data',
    weather_summary: 'Clear',
    temp_high: 10,
    temp_low: 2,
    precip_mm: 0,
    wind_speed: 3,
  });
  const forecast = {
    forecast_days: [insufficientDay('2026-09-27'), insufficientDay('2026-09-28')],
    today: insufficientDay('2026-09-27'),
    elevation_meters: null,
  };
  const insufficientHour = (hour) => ({
    hour,
    risk_score: null,
    color_code: 'gray',
    data_status: 'insufficient_data',
    is_climbable: null,
    conditions_summary: 'No risk estimate',
    temperature: 2,
    wind_speed: 3,
    precipitation: 0,
  });
  const timeOfDay = {
    data_status: 'insufficient_data',
    base_daily_risk: null,
    hourly_data: [insufficientHour(9), insufficientHour(10)],
    climbing_windows: [],
    best_window: null,
  };

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.includes('/forecast')) return Promise.resolve(jsonResponse(forecast));
      if (url.includes('/time-of-day')) return Promise.resolve(jsonResponse(timeOfDay));
      return Promise.resolve(jsonResponse(route(1, 'Route A')));
    }));
  });

  const neutral = { backgroundColor: NO_RISK_HEX, color: readableTextOn(NO_RISK_HEX) };

  it('forecast chips read "Insufficient data" in neutral grey, never a number', async () => {
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: '7-Day Forecast' }));
    await screen.findByText(/Weather Summary/);

    const dialog = screen.getByRole('dialog');
    const chips = within(dialog).getAllByText('Insufficient data');
    expect(chips).toHaveLength(2);
    for (const chip of chips) expect(chip.closest('.MuiChip-root')).toHaveStyle(neutral);
    expect(within(dialog).queryByText('N/A', { selector: '.MuiChip-label' })).toBeNull();
  });

  it('hourly view explains insufficient data and marks no hour climbable', async () => {
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Time of Day' }));
    await screen.findByText(/Hourly Conditions Detail/);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(INSUFFICIENT_DATA_MESSAGE)).toBeInTheDocument();
    const chips = within(dialog).getAllByText('Insufficient data', { selector: '.MuiChip-label' });
    expect(chips).toHaveLength(2);
    for (const chip of chips) expect(chip.closest('.MuiChip-root')).toHaveStyle(neutral);
    expect(within(dialog).queryByText(/Best Climbing Window/)).toBeNull();
  });

  it('CSV leaves the risk cells empty and names the status', async () => {
    const parts = [];
    vi.stubGlobal('Blob', class {
      constructor(content) { parts.push(content.join('')); }
    });
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: () => {} });
    const user = userEvent.setup();
    render(
      <Modal routeData={{ ...route(1, 'Route A'), color_code: 'gray', data_status: 'insufficient_data' }} />,
    );
    await user.click(screen.getByRole('tab', { name: '7-Day Forecast' }));
    await screen.findByText(/Weather Summary/);
    await user.click(screen.getByTitle('Export Analytics Data'));
    await user.click(await screen.findByText(/Export as CSV/));

    expect(parts).toHaveLength(1);
    const csv = parts[0];
    expect(csv).toContain('Risk Score,\n');
    expect(csv).toContain('Data Status,insufficient_data\n');
    expect(csv).toContain('2026-09-27,,"Clear"');
    expect(csv).not.toMatch(/Risk Score,0/);
  });
});

describe('RouteAnalyticsModal breakdown, trends and 1-decimal chips (fix round 1)', () => {
  const breakdown = {
    route_id: 1,
    risk_score: null,
    color_code: 'gray',
    data_status: 'insufficient_data',
    message: 'Too little evidence to estimate risk yet',
    num_contributing_accidents: 0,
    factors: [],
    top_accidents: [],
  };
  const okDay = (date, risk_score) => ({
    date, risk_score, color_code: 'yellow', data_status: 'ok', weather_summary: 'Clear',
    temp_high: 10, temp_low: 2, precip_mm: 0, wind_speed: 3,
  });
  const forecast = { forecast_days: [okDay('2026-09-27', 30), okDay('2026-09-28', 42.5)], elevation_meters: null };
  const scored = Array.from({ length: 30 }, (_, i) => ({
    date: `2026-08-${String(i + 1).padStart(2, '0')}`, risk_score: 20, color_code: 'green', data_status: 'ok',
  }));
  const historical = {
    reference_date: '2026-09-27',
    historical_predictions: [
      ...scored,
      { date: '2026-09-01', risk_score: null, color_code: 'gray', data_status: 'insufficient_data' },
    ],
    days_available: 30,
    summary: { avg_risk: 20, min_risk: 20, max_risk: 20 },
    trend: { direction: 'stable', description: 'Risk is about the same in the latest 7 scored days vs the earliest 7 scored days' },
  };

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.includes('/risk-breakdown')) return Promise.resolve(jsonResponse(breakdown));
      if (url.includes('/forecast')) return Promise.resolve(jsonResponse(forecast));
      if (url.includes('/historical-trends')) return Promise.resolve(jsonResponse(historical));
      return Promise.resolve(jsonResponse(route(1, 'Route A')));
    }));
  });

  it('an insufficient breakdown shows the message and no factors or zero-contribution slice', async () => {
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Risk Breakdown' }));
    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText(INSUFFICIENT_DATA_MESSAGE)).toBeInTheDocument();
    expect(within(dialog).queryByText(/No Data/)).toBeNull();
    expect(within(dialog).queryByText(/Factor Contributions|pts/)).toBeNull();
  });

  it('forecast chips show one decimal', async () => {
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: '7-Day Forecast' }));
    await screen.findByText(/Weather Summary/);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('30.0', { selector: '.MuiChip-label' })).toBeInTheDocument();
    expect(within(dialog).getByText('42.5', { selector: '.MuiChip-label' })).toBeInTheDocument();
  });

  it('counts scored days only and names the trend window as scored days', async () => {
    const user = userEvent.setup();
    render(<Modal routeData={route(1, 'Route A')} />);
    await user.click(screen.getByRole('tab', { name: 'Risk Trends' }));
    const dialog = screen.getByRole('dialog');
    const label = await within(dialog).findByText('Days Scored');
    expect(label.parentElement).toHaveTextContent('30');
    expect(label.parentElement).not.toHaveTextContent('31');
    expect(within(dialog).getByText(/Risk is relatively stable \(latest 7 scored days/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/past 30 days/)).toBeNull();
  });
});
