import { describe, expect, it } from 'vitest';
import { render, screen } from '../test/utils';
import RiskLegend from './RiskLegend';
import { LEGEND_PANEL_BG, LEGEND_SWATCHES, NO_RISK_HEX, legendSwatchBorder } from '../utils/riskUtils';

describe('RiskLegend', () => {
  it('shows a hatched Low confidence swatch', () => {
    render(<RiskLegend />);
    expect(screen.getByText('Low confidence')).toBeInTheDocument();
    expect(screen.getByTestId('legend-swatch-Low confidence')).toHaveAttribute('data-hatch', 'true');
  });

  it('shows a gray, hatched Not enough evidence swatch', () => {
    render(<RiskLegend />);
    const swatch = screen.getByTestId('legend-swatch-Not enough evidence');
    expect(swatch).toHaveStyle({ backgroundColor: NO_RISK_HEX });
    expect(swatch).toHaveAttribute('data-hatch', 'true');
    expect(screen.getByText('Not enough evidence')).toBeInTheDocument();
  });

  it('labels the lowest band "Lower than typical" and never says safe or Low Risk', () => {
    const { container } = render(<RiskLegend />);
    expect(screen.getByText('Lower than typical for this type')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\bsafe\b|low risk/i);
  });

  it('shows Unavailable with no fill, distinct from the gray', () => {
    render(<RiskLegend />);
    const swatch = screen.getByTestId('legend-swatch-Unavailable');
    expect(swatch).not.toHaveStyle({ backgroundColor: NO_RISK_HEX });
    expect(swatch).toHaveAttribute('data-hatch', 'false');
  });

  it('borders every swatch with its contrast-checked colour', () => {
    render(<RiskLegend />);
    for (const { label, fill } of LEGEND_SWATCHES) {
      const swatch = screen.getByTestId(`legend-swatch-${label}`);
      expect(swatch).toHaveStyle({ borderColor: legendSwatchBorder(fill, LEGEND_PANEL_BG) });
      if (fill) expect(swatch).toHaveStyle({ backgroundColor: fill });
    }
  });
});
