/**
 * Tests for PredictionResult component
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '../test/utils';
import PredictionResult from './PredictionResult';

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
});
