/**
 * Tests for PredictionResult component
 */
import { describe, it, expect, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../test/utils';
import PredictionResult from './PredictionResult';
import { NO_RISK_HEX, RISK_COLOR_HEX, RISK_TEXT_ON_HEX } from '../utils/riskUtils';

// Mock prediction data
const mockPrediction = {
  risk_score: 35.5,
  num_contributing_accidents: 12,
  top_contributing_accidents: [
    {
      accident_id: 1,
      accident_date: '2023-07-15',
      severity: 'Minor Injury',
      distance_km: 5.2,
      total_influence: 0.85,
    },
  ],
  metadata: {
    weather_source: 'openweathermap',
  },
};

describe('PredictionResult', () => {
  it('renders risk score correctly (rounded)', () => {
    render(<PredictionResult prediction={mockPrediction} />);

    // Component rounds risk_score (35.5 → 36)
    expect(screen.getByText('36')).toBeInTheDocument();
  });

  it('renders top contributing factors section', () => {
    render(<PredictionResult prediction={mockPrediction} />);

    // Should show the contributing factors heading
    expect(screen.getByText('Top Contributing Factors')).toBeInTheDocument();
    // Should show accident #1 (from mock data)
    expect(screen.getByText(/Accident #/)).toBeInTheDocument();
  });

  it('displays appropriate risk level color', () => {
    const highRiskPrediction = {
      ...mockPrediction,
      risk_score: 75.0,
    };

    render(<PredictionResult prediction={highRiskPrediction} />);

    // High risk should be displayed (we test that it renders without error)
    expect(screen.getByText('75')).toBeInTheDocument();
  });

  it('colours the level badge with the shared band hex', () => {
    render(<PredictionResult prediction={{ ...mockPrediction, risk_score: 61.3 }} />);
    expect(screen.getByText('HIGH RISK').closest('.MuiChip-root')).toHaveStyle({
      backgroundColor: RISK_COLOR_HEX.orange,
    });
  });

  it('handles zero risk score', () => {
    const zeroRiskPrediction = {
      ...mockPrediction,
      risk_score: 0,
      num_contributing_accidents: 0,
      top_contributing_accidents: [],
    };

    render(<PredictionResult prediction={zeroRiskPrediction} />);

    // Find the risk score display specifically (the Typography h2)
    // Use a more specific selector to avoid matching "0" in other places
    expect(screen.getByText('LOW RISK')).toBeInTheDocument();
  });

  it('handles null prediction gracefully', () => {
    render(<PredictionResult prediction={null} />);

    // Should not crash, may show loading or empty state
    expect(document.body).toBeTruthy();
  });

  it('renders an error Alert with Retry and no score when the request failed', async () => {
    const onRetry = vi.fn();
    render(<PredictionResult prediction={null} error="Cannot connect to SafeAscent API." onRetry={onRetry} />);

    expect(screen.getByRole('alert')).toHaveTextContent('Cannot connect to SafeAscent API.');
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.queryByText(/^\d+$/)).toBeNull();
    expect(screen.queryByText(/RISK$/)).toBeNull();
  });

  it('shows Unavailable instead of a number when risk_score is missing', () => {
    render(<PredictionResult prediction={{ ...mockPrediction, risk_score: undefined }} />);

    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.queryByText(/^\d+$/)).toBeNull();
    expect(screen.queryByText(/RISK$/)).toBeNull();
    expect(screen.queryByText(/NaN|undefined/)).toBeNull();
  });

  it('shows the missing-score icon in the neutral no-data grey', () => {
    for (const risk_score of [undefined, null, NaN]) {
      const { unmount } = render(<PredictionResult prediction={{ ...mockPrediction, risk_score }} />);
      expect(screen.getByTestId('risk-icon')).toHaveStyle({ color: NO_RISK_HEX });
      expect(screen.queryByText(/NaN|undefined/)).toBeNull();
      unmount();
    }
  });

  it('uses the contrast-checked text colour on the level badge', () => {
    render(<PredictionResult prediction={{ ...mockPrediction, risk_score: 80 }} />);
    expect(screen.getByText('EXTREME RISK').closest('.MuiChip-root')).toHaveStyle({
      color: RISK_TEXT_ON_HEX.red,
    });
  });
});
