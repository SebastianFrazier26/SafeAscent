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
