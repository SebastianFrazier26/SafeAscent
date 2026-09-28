import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../test/utils';
import RouteAnalyticsModal from './RouteAnalyticsModal';
import { useRouteSafety } from '../hooks/useRouteSafety';
import { fetchRouteSafety } from '../services/api';
import { RISK_COLOR_HEX } from '../utils/riskUtils';

vi.mock('../services/api', () => ({ fetchRouteSafety: vi.fn() }));

function Harness() {
  const { state, retry } = useRouteSafety(42, '2026-09-27');
  const ok = state?.status === 'ok' ? state.data : null;
  return (
    <RouteAnalyticsModal
      open
      onClose={() => {}}
      selectedDate="2026-09-27"
      routeData={{
        route_id: 42,
        name: 'Test Route',
        mountain_name: 'Test Crag',
        type: 'Ice',
        grade: 'WI3',
        latitude: 44.1,
        longitude: -73.9,
        elevation_meters: null,
        risk_score: ok ? ok.risk_score : null,
        color_code: ok ? ok.color_code : null,
        mp_route_id: 42,
      }}
      safety={state}
      onRetrySafety={retry}
    />
  );
}

beforeEach(() => {
  // Tab data requests stay pending so only the safety state drives what renders.
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
  vi.mocked(fetchRouteSafety).mockReset();
});

afterEach(() => vi.unstubAllGlobals());

describe('RouteAnalyticsModal risk display', () => {
  it('shows an error Alert with Retry and never a numeric risk when the safety fetch rejects', async () => {
    vi.mocked(fetchRouteSafety).mockRejectedValueOnce(new Error('Cannot connect to SafeAscent API.'));
    render(<Harness />);

    expect(await screen.findByText(/couldn.t load the risk score/i)).toBeInTheDocument();
    expect(screen.getByText(/Cannot connect to SafeAscent API\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByText('Risk: Unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/0\.0/)).toBeNull();
    expect(screen.queryByText(/Risk:\s*\d/)).toBeNull();
  });

  it('Retry refetches and then shows the real score', async () => {
    vi.mocked(fetchRouteSafety)
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({
        route_id: 42,
        route_name: 'Test Route',
        target_date: '2026-09-27',
        risk_score: 42.3,
        color_code: 'yellow',
      });
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(await screen.findByRole('button', { name: /retry/i }));
    expect(await screen.findByText('Risk: 42.3/100')).toBeInTheDocument();
    expect(fetchRouteSafety).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/couldn.t load the risk score/i)).toBeNull();
  });

  it('colours an ok score with the shared band hex', async () => {
    vi.mocked(fetchRouteSafety).mockResolvedValueOnce({
      route_id: 42,
      route_name: 'Test Route',
      target_date: '2026-09-27',
      risk_score: 61.3,
      color_code: 'orange',
    });
    render(<Harness />);

    const label = await screen.findByText('Risk: 61.3/100');
    expect(label.closest('.MuiChip-root')).toHaveStyle({ backgroundColor: RISK_COLOR_HEX.orange });
  });

  it('shows a loading label, not a number, while the score is in flight', () => {
    vi.mocked(fetchRouteSafety).mockReturnValueOnce(new Promise(() => {}));
    render(<Harness />);
    expect(screen.getByText('Risk: loading…')).toBeInTheDocument();
    expect(screen.queryByText(/Risk:\s*\d/)).toBeNull();
  });
});
