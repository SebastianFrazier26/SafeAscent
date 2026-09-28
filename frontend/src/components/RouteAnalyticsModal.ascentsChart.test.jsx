import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../test/utils';
import RouteAnalyticsModal from './RouteAnalyticsModal';

// ResponsiveContainer renders nothing at jsdom's zero size, so chart props are asserted
// through light stand-ins instead of the SVG.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal();
  const Stub = (name) => ({ children, ...props }) => (
    <div data-chart={name} data-props={JSON.stringify(props)}>{children}</div>
  );
  return {
    ...actual,
    ResponsiveContainer: ({ children }) => <div>{children}</div>,
    BarChart: Stub('BarChart'),
    Bar: Stub('Bar'),
    YAxis: Stub('YAxis'),
  };
});

const route = {
  route_id: 1, name: 'Route A', mountain_name: 'Test Crag', type: 'Ice', grade: 'WI3',
  latitude: 44.1, longitude: -73.9, elevation_meters: null, risk_score: null, color_code: null, mp_route_id: 1,
};
const ascents = {
  has_data: true,
  total_ascents: 40,
  total_accidents: 2,
  peak_month: 'Jan',
  monthly_stats: [
    { month: 'Jan', ascent_count: 30, accident_count: 1 },
    { month: 'Feb', ascent_count: 10, accident_count: 1 },
  ],
};
const jsonResponse = (body) => ({ ok: true, json: async () => body, text: async () => '' });

describe('Ascents charts', () => {
  it('never draw accidents in red or on a second, independently scaled axis', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve(jsonResponse(
      url.endsWith('/ascent-analytics') ? ascents : route,
    ))));
    const user = userEvent.setup();
    const { container } = render(
      <RouteAnalyticsModal open onClose={() => {}} selectedDate="2026-09-27" routeData={route}
        safety={{ status: 'loading' }} onRetrySafety={() => {}} />,
    );
    await user.click(screen.getByRole('tab', { name: 'Ascents' }));
    await screen.findByText(/Counts by Month/);

    const props = (el) => JSON.parse(el.getAttribute('data-props'));
    const barCharts = container.ownerDocument.querySelectorAll('[data-chart="BarChart"]');
    expect(barCharts.length).toBeGreaterThanOrEqual(2);
    for (const chart of barCharts) {
      const axes = chart.querySelectorAll('[data-chart="YAxis"]');
      expect(axes).toHaveLength(1);
      expect(props(axes[0]).yAxisId).toBeUndefined();
      expect(props(axes[0]).orientation).not.toBe('right');
      const bars = chart.querySelectorAll('[data-chart="Bar"]');
      expect(bars).toHaveLength(1);
    }
    const accidentBar = [...container.ownerDocument.querySelectorAll('[data-chart="Bar"]')]
      .map(props).find((p) => p.dataKey === 'accident_count');
    expect(accidentBar).toBeDefined();
    for (const bar of container.ownerDocument.querySelectorAll('[data-chart="Bar"]')) {
      const { fill } = props(bar);
      expect(fill.toLowerCase()).not.toMatch(/#f44336|#d32f2f|#e53935|#c62828|red|#4caf50|#2e7d32|green/);
    }
    vi.unstubAllGlobals();
  });
});
