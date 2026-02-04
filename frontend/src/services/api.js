/**
 * API Service for SafeAscent Backend
 *
 * Handles all communication with the FastAPI backend.
 */
import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

// Create axios instance with default config
const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000, // 30 second timeout
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add request interceptor for logging (development only)
api.interceptors.request.use(
  (config) => {
    if (import.meta.env.DEV) {
      console.log('🚀 API Request:', config.method.toUpperCase(), config.url, config.data);
    }
    return config;
  },
  (error) => {
    console.error('❌ API Request Error:', error);
    return Promise.reject(error);
  }
);

// Add response interceptor for logging and error handling
api.interceptors.response.use(
  (response) => {
    if (import.meta.env.DEV) {
      console.log('✅ API Response:', response.config.url, response.data);
    }
    return response;
  },
  (error) => {
    console.error('❌ API Response Error:', error.response?.data || error.message);
    return Promise.reject(error);
  }
);

/**
 * Predict route safety
 *
 * @param {Object} params - Prediction parameters
 * @param {number} params.latitude - Route latitude (-90 to 90)
 * @param {number} params.longitude - Route longitude (-180 to 180)
 * @param {string} params.route_type - Type of climbing route (alpine, trad, sport, ice, mixed, boulder)
 * @param {string} params.planned_date - Date in YYYY-MM-DD format
 * @param {number} [params.elevation_meters] - Optional elevation in meters (auto-detected if omitted)
 * @param {number} [params.search_radius_km] - Optional search radius (default: 500km)
 * @returns {Promise<Object>} Prediction result with risk score and confidence
 */
export const predictRouteSafety = async (params) => {
  try {
    const response = await api.post('/predict', params);
    return response.data;
  } catch (error) {
    // Transform error for better UX
    if (error.response?.status === 422) {
      throw new Error('Invalid prediction parameters. Please check your input.');
    } else if (error.code === 'ECONNABORTED') {
      throw new Error('Request timed out. The server may be slow or unavailable.');
    } else if (!error.response) {
      throw new Error('Cannot connect to SafeAscent API. Please check your connection.');
    }
    throw error;
  }
};

/**
 * Fetch nearby accidents (for map visualization)
 *
 * @param {number} latitude - Center latitude
 * @param {number} longitude - Center longitude
 * @param {number} [radius_km=50] - Search radius in kilometers
 * @returns {Promise<Array>} Array of accidents
 */
export const fetchNearbyAccidents = async (latitude, longitude, radius_km = 50) => {
  try {
    const response = await api.get('/accidents', {
      params: {
        latitude,
        longitude,
        radius_km,
        limit: 100, // Max accidents to show on map
      },
    });
    return response.data;
  } catch (error) {
    console.error('Failed to fetch nearby accidents:', error);
    return []; // Return empty array on error (graceful degradation)
  }
};

/**
 * Health check - verify backend is running
 *
 * @returns {Promise<boolean>} True if backend is healthy
 */
export const healthCheck = async () => {
  try {
    const response = await api.get('/health');
    return response.status === 200;
  } catch {
    return false;
  }
};

export default api;
