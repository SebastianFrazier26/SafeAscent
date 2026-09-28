import { describe, expect, it } from 'vitest';
import { render, screen } from '../test/utils';
import RiskLegend from './RiskLegend';
import { LEGEND_PANEL_BG, LEGEND_SWATCHES, NO_RISK_HEX, legendSwatchBorder } from '../utils/riskUtils';

describe('RiskLegend', () => {
  it('lists an Insufficient data swatch in the neutral grey', () => {
    render(<RiskLegend />);
    const swatch = screen.getByTestId('legend-swatch-Insufficient data');
    expect(swatch).toHaveStyle({ backgroundColor: NO_RISK_HEX });
    expect(screen.getByText('Insufficient data')).toBeInTheDocument();
  });

  it('borders every swatch with its contrast-checked colour', () => {
    render(<RiskLegend />);
    for (const { label, fill } of LEGEND_SWATCHES) {
      const swatch = screen.getByTestId(`legend-swatch-${label}`);
      expect(swatch).toHaveStyle({
        backgroundColor: fill,
        borderColor: legendSwatchBorder(fill, LEGEND_PANEL_BG),
      });
    }
  });
});
