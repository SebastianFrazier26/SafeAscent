/**
 * Test Utilities
 * Custom render function and test helpers for React Testing Library
 */
import { render } from '@testing-library/react';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { LocalizationProvider } from '@mui/x-date-pickers';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';

// Create a test theme
const theme = createTheme({
  palette: {
    mode: 'light',
  },
});

/**
 * Custom render function that wraps components with providers
 * Use this instead of @testing-library/react's render
 */
function customRender(ui, options = {}) {
  const AllProviders = ({ children }) => (
    <ThemeProvider theme={theme}>
      <LocalizationProvider dateAdapter={AdapterDateFns}>
        {children}
      </LocalizationProvider>
    </ThemeProvider>
  );

  return render(ui, { wrapper: AllProviders, ...options });
}

// Re-export everything from testing-library
export * from '@testing-library/react';

// Override render with our custom version
export { customRender as render };

/**
 * Mock prediction response for testing
 */
export const mockPredictionResponse = {
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
    {
      accident_id: 2,
      accident_date: '2022-08-20',
      severity: 'Serious Injury',
      distance_km: 12.1,
      total_influence: 0.62,
    },
  ],
  metadata: {
    weather_source: 'openweathermap',
    algorithm_version: '1.0',
  },
};

/**
 * Mock route data for testing
 */
export const mockRoute = {
  id: 1,
  name: 'Keyhole Route',
  mountain_id: 1,
  mountain_name: 'Longs Peak',
  route_type: 'alpine',
  difficulty: 'Class 3',
  latitude: 40.2549,
  longitude: -105.6426,
  elevation_gain: 1450,
};

/**
 * Mock mountain data for testing
 */
export const mockMountain = {
  id: 1,
  name: 'Longs Peak',
  latitude: 40.2549,
  longitude: -105.6426,
  elevation: 4346,
  range: 'Rocky Mountains',
  state: 'Colorado',
};
