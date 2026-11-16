import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '../test/utils';
import RouteAnalyticsModal from './RouteAnalyticsModal';
import { useRouteSafety } from '../hooks/useRouteSafety';
import { SCORE_UNAVAILABLE_MESSAGE, fetchRouteSafety } from '../services/api';
import {
  INSUFFICIENT_DATA_MESSAGE,
  LOWEST_BAND_HEX,
  NO_RISK_HEX,
  RISK_COLOR_HEX,
  RISK_DISCLAIMER,
} from '../utils/riskUtils';

vi.mock('../services/api', async (importOriginal) => ({
  ...(await importOriginal()),
  fetchRouteSafety: vi.fn(),
}));

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
        color_code: ok ? ok.color_code : state?.status === 'insufficient' ? 'gray' : null,
        data_status: state?.status === 'insufficient' ? 'insufficient_data' : ok ? 'ok' : null,
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
    const chip = screen.getByText('Risk: Unavailable');
    expect(chip.closest('.MuiChip-root')).not.toHaveStyle({ backgroundColor: NO_RISK_HEX });
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
        data_status: 'ok',
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
      data_status: 'ok',
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

  it('shows insufficient data as a neutral info message, not an error, with no Retry and no number', async () => {
    vi.mocked(fetchRouteSafety).mockResolvedValueOnce({
      route_id: 42,
      route_name: 'Test Route',
      target_date: '2026-09-27',
      risk_score: null,
      color_code: 'gray',
      data_status: 'insufficient_data',
    });
    render(<Harness />);

    expect(await screen.findByText(INSUFFICIENT_DATA_MESSAGE)).toBeInTheDocument();
    const chip = screen.getByText('Risk: Not enough evidence');
    expect(chip.closest('.MuiChip-root')).toHaveStyle({ backgroundColor: NO_RISK_HEX });
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
    expect(screen.queryByText(/couldn.t load the risk score/i)).toBeNull();
    expect(screen.queryByText(/Risk:\s*\d/)).toBeNull();
  });

  it('shows a low-confidence score with its number, range, label, hatch and the disclaimer', async () => {
    vi.mocked(fetchRouteSafety).mockResolvedValueOnce(stored({ risk_score: 40, color_code: 'yellow', confidence: 'low', score_range: [20, 60] }));
    render(<Harness />);

    const summary = await screen.findByTestId('risk-summary');
    expect(within(summary).getByText('40.0 (likely 20.0–60.0)')).toBeInTheDocument();
    expect(within(summary).getByText('Low confidence')).toBeInTheDocument();
    expect(within(summary).getByText(RISK_DISCLAIMER)).toBeInTheDocument();
    expect(within(summary).getByText('≈ 2 local, 12 from similar routes elsewhere')).toBeInTheDocument();
    const chip = within(summary).getByTestId('risk-summary-chip');
    expect(chip).toHaveStyle({ backgroundColor: RISK_COLOR_HEX.yellow });
    expectHatchDot(chip);
    expect(within(chip).getByText('40.0 (likely 20.0–60.0)')).not.toContainElement(chip.querySelector('svg'));
    const header = screen.getByText('Risk: 40.0/100 (low confidence)').closest('.MuiChip-root');
    expectHatchDot(header);
  });

  it('shows a medium-confidence score with its range and no hatch', async () => {
    vi.mocked(fetchRouteSafety).mockResolvedValueOnce(stored({ risk_score: 61.3, color_code: 'orange', confidence: 'medium' }));
    render(<Harness />);

    const summary = await screen.findByTestId('risk-summary');
    expect(within(summary).getByText('61.3 (likely 20.0–70.0)')).toBeInTheDocument();
    expect(within(summary).getByText('Medium confidence')).toBeInTheDocument();
    expect(within(summary).getByTestId('risk-summary-chip').querySelector('pattern')).toBeNull();
    expect(screen.getByText('Risk: 61.3/100').closest('.MuiChip-root').querySelector('pattern')).toBeNull();
  });

  it('draws the lowest band in neutral slate, labelled lower than typical, never Low Risk', async () => {
    vi.mocked(fetchRouteSafety).mockResolvedValueOnce(
      stored({ risk_score: 10, color_code: 'green', confidence: 'high', score_range: [4, 18] }),
    );
    render(<Harness />);

    const summary = await screen.findByTestId('risk-summary');
    expect(within(summary).getByTestId('risk-summary-chip')).toHaveStyle({ backgroundColor: LOWEST_BAND_HEX });
    expect(within(summary).getByText(/Lower than typical for this type/)).toBeInTheDocument();
    expect(screen.queryByText(/low risk|\bsafe\b/i)).toBeNull();
  });

  it('shows insufficient evidence as the gray chip with no digits at all', async () => {
    vi.mocked(fetchRouteSafety).mockResolvedValueOnce({
      route_id: 42,
      route_name: 'Test Route',
      target_date: '2026-09-27',
      risk_score: null,
      color_code: 'gray',
      data_status: 'insufficient_data',
      insufficient_reason: 'no_evidence',
      model_version: 'v2',
    });
    render(<Harness />);

    const summary = await screen.findByTestId('risk-summary');
    const chip = within(summary).getByTestId('risk-summary-chip');
    expect(chip).toHaveTextContent(INSUFFICIENT_DATA_MESSAGE);
    expect(chip).toHaveStyle({ backgroundColor: NO_RISK_HEX });
    expectHatchDot(chip);
    expect(summary.textContent).not.toMatch(/\d/);
    expect(within(summary).queryByText(/likely|confidence/i)).toBeNull();
    expect(screen.getByText('Risk: Not enough evidence').textContent).not.toMatch(/\d/);
  });

  it('shows a 404 as Unavailable: an em-dash, not gray, not 0, no Retry', async () => {
    vi.mocked(fetchRouteSafety).mockRejectedValueOnce(new Error(SCORE_UNAVAILABLE_MESSAGE));
    render(<Harness />);

    const summary = await screen.findByTestId('risk-summary');
    expect(within(summary).getByText(SCORE_UNAVAILABLE_MESSAGE)).toBeInTheDocument();
    const chip = within(summary).getByTestId('risk-summary-chip');
    expect(chip).toHaveTextContent('—');
    expect(chip).not.toHaveStyle({ backgroundColor: NO_RISK_HEX });
    expect(chip.querySelector('pattern')).toBeNull();
    expect(summary.textContent).not.toMatch(/\d/);
    const header = screen.getByText('Risk: Unavailable');
    expect(header.closest('.MuiChip-root')).not.toHaveStyle({ backgroundColor: NO_RISK_HEX });
    expect(screen.queryByRole('button', { name: /retry/i })).toBeNull();
  });
});

function stored(overrides) {
  return {
    route_id: 42,
    route_name: 'Test Route',
    target_date: '2026-09-27',
    data_status: 'ok',
    score_range: [20, 70],
    model_version: 'v2',
    evidence: { geo: 2.4, similar: 11.6 },
    relative_rate: null,
    per_10k_logged_ascents: null,
    per_10k_reason: null,
    ...overrides,
  };
}

// The hatch is a real SVG pattern on a leading dot, filled through, not just a flag.
function expectHatchDot(chip) {
  const pattern = chip.querySelector('svg pattern');
  expect(pattern).not.toBeNull();
  expect(chip.querySelector(`rect[fill="url(#${pattern.id})"]`)).not.toBeNull();
  expect(chip.querySelector('.MuiChip-label').querySelector('svg')).toBeNull();
}
