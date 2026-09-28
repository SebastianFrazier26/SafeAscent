/**
 * API client for the SafeAscent backend. Responses that drive a risk number are
 * validated here, so a malformed body becomes an error, never a default score.
 */
import axios, { type AxiosInstance } from 'axios';

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

api.interceptors.request.use(
  (config) => {
    if (import.meta.env.DEV) {
      console.log('🚀 API Request:', config.method?.toUpperCase(), config.url, config.data);
    }
    return config;
  },
  (error: unknown) => {
    console.error('❌ API Request Error:', error);
    return Promise.reject(error);
  },
);

api.interceptors.response.use(
  (response) => {
    if (import.meta.env.DEV) {
      console.log('✅ API Response:', response.config.url, response.data);
    }
    return response;
  },
  (error: unknown) => {
    const detail = axios.isAxiosError(error) ? error.response?.data ?? error.message : error;
    console.error('❌ API Response Error:', detail);
    return Promise.reject(error);
  },
);

export type RiskColorCode = 'green' | 'yellow' | 'orange' | 'red';
export type DataStatus = 'ok' | 'insufficient_data';

/**
 * A risk result is either a real 0-100 score with its band colour, or (owner decision
 * 2026-09-28) insufficient data: too little evidence, so no number and gray.
 */
export type ScoredRisk = { risk_score: number; color_code: RiskColorCode; data_status: 'ok' };
export type InsufficientRisk = { risk_score: null; color_code: 'gray'; data_status: 'insufficient_data' };
export type RiskResult = ScoredRisk | InsufficientRisk;

export type SafetyResponse = {
  route_id: number;
  route_name: string;
  target_date: string;
} & RiskResult;

export interface PredictionParams {
  latitude: number;
  longitude: number;
  route_type: string;
  planned_date: string;
  elevation_meters?: number;
  search_radius_km?: number;
  route_grade?: string;
}

export interface ContributingAccident {
  accident_id: number;
  total_influence: number;
  distance_km: number;
  days_ago: number;
  spatial_weight: number;
  temporal_weight: number;
  elevation_weight: number;
  weather_weight: number;
  route_type_weight: number;
  severity_weight: number;
  grade_weight?: number;
}

export type PredictionResponse = {
  num_contributing_accidents: number;
  top_contributing_accidents: ContributingAccident[];
  metadata: Record<string, unknown>;
} & RiskResult;

const COLOR_CODES: readonly string[] = ['green', 'yellow', 'orange', 'red'];

// Exactly the two legal shapes: a gray colour with a number, or a number with an
// insufficient status, is malformed (the backend schema rejects both too).
const isRiskResult = (body: Record<string, unknown>): boolean => {
  if (body.data_status === 'insufficient_data') {
    return body.risk_score === null && body.color_code === 'gray';
  }
  return (
    body.data_status === 'ok' &&
    typeof body.risk_score === 'number' &&
    Number.isFinite(body.risk_score) &&
    body.risk_score >= 0 &&
    body.risk_score <= 100 &&
    typeof body.color_code === 'string' &&
    COLOR_CODES.includes(body.color_code)
  );
};

export const isSafetyResponse = (value: unknown): value is SafetyResponse => {
  if (typeof value !== 'object' || value === null) return false;
  const body = value as Record<string, unknown>;
  return (
    typeof body.route_id === 'number' &&
    typeof body.route_name === 'string' &&
    typeof body.target_date === 'string' &&
    isRiskResult(body)
  );
};

export const isPredictionResponse = (value: unknown): value is PredictionResponse => {
  if (typeof value !== 'object' || value === null) return false;
  const body = value as Record<string, unknown>;
  return (
    typeof body.num_contributing_accidents === 'number' &&
    Array.isArray(body.top_contributing_accidents) &&
    typeof body.metadata === 'object' &&
    body.metadata !== null &&
    isRiskResult(body)
  );
};

const toReadableError = (error: unknown): Error => {
  if (axios.isAxiosError(error)) {
    if (error.code === 'ECONNABORTED') {
      return new Error('Request timed out. The server may be slow or unavailable.');
    }
    if (!error.response) {
      return new Error('Cannot connect to SafeAscent API. Please check your connection.');
    }
    return new Error(`SafeAscent API error (HTTP ${error.response.status}).`);
  }
  return error instanceof Error ? error : new Error('Unexpected error.');
};

export const fetchRouteSafety = async (routeId: number, targetDate: string): Promise<SafetyResponse> => {
  let data: unknown;
  try {
    const response = await api.post(`/mp-routes/${routeId}/safety`, null, {
      params: { target_date: targetDate, bypass_cache: true },
    });
    data = response.data;
  } catch (error) {
    throw toReadableError(error);
  }
  if (!isSafetyResponse(data)) {
    throw new Error('Malformed safety response from the API.');
  }
  return data;
};

export const predictRouteSafety = async (params: PredictionParams): Promise<PredictionResponse> => {
  let data: unknown;
  try {
    const response = await api.post('/predict', params);
    data = response.data;
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 422) {
      throw new Error('Invalid prediction parameters. Please check your input.');
    }
    throw toReadableError(error);
  }
  if (!isPredictionResponse(data)) {
    throw new Error('Malformed prediction response from the API.');
  }
  return data;
};

// Names must match GET /accidents (lat, lon, radius_km); the API rejects a partial set with
// 422 rather than ignoring it. A failure throws: an empty list would read as "no accidents".
export const fetchNearbyAccidents = async (
  latitude: number,
  longitude: number,
  radiusKm = 50,
): Promise<unknown[]> => {
  let data: unknown;
  try {
    ({ data } = await api.get<unknown>('/accidents', {
      params: { lat: latitude, lon: longitude, radius_km: radiusKm, limit: 100 },
    }));
  } catch (error) {
    throw toReadableError(error);
  }
  const body = data as { total?: unknown; data?: unknown } | null;
  if (!body || typeof body.total !== 'number' || !Array.isArray(body.data)) {
    throw new Error('Malformed accident list from the API.');
  }
  return body.data;
};

export const healthCheck = async (): Promise<boolean> => {
  try {
    const response = await api.get('/health');
    return response.status === 200;
  } catch {
    return false;
  }
};

export default api;
