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

export type Confidence = 'high' | 'medium' | 'low';
export type Interval = { mean: number; lo: number; hi: number };

/**
 * A risk result is either a real 0-100 score with its band colour, or (owner decision
 * 2026-09-28) insufficient data: too little evidence, so no number and gray. The Legacy
 * shapes are what a backend that predates the stored-score contract still sends; they are
 * accepted so a frontend deployed first keeps working, and never given invented fields.
 */
export type ScoredRisk = {
  risk_score: number;
  color_code: RiskColorCode;
  data_status: 'ok';
  confidence: Confidence;
  score_range: [number, number];
  model_version: string;
  evidence: { geo: number; similar: number };
  relative_rate: Interval | null;
  per_10k_logged_ascents: Interval | null;
  per_10k_reason: string | null;
};
export type InsufficientRisk = {
  risk_score: null;
  color_code: 'gray';
  data_status: 'insufficient_data';
  insufficient_reason: 'no_evidence';
  model_version: string;
};
export type LegacyScoredRisk = { risk_score: number; color_code: RiskColorCode; data_status: 'ok' };
export type LegacyInsufficientRisk = { risk_score: null; color_code: 'gray'; data_status: 'insufficient_data' };
export type RiskResult = ScoredRisk | InsufficientRisk | LegacyScoredRisk | LegacyInsufficientRisk;

export type SafetyResponse = {
  route_id: number;
  route_name: string;
  target_date: string;
} & RiskResult;

export const isScored = (r: RiskResult): r is ScoredRisk | LegacyScoredRisk =>
  r.data_status === 'ok' && typeof r.risk_score === 'number';

export const isInsufficient = (r: RiskResult): r is InsufficientRisk | LegacyInsufficientRisk =>
  r.data_status === 'insufficient_data';

export const isStoredScore = (r: RiskResult): r is ScoredRisk =>
  r.data_status === 'ok' && 'confidence' in r && r.confidence != null;

const COLOR_CODES: readonly string[] = ['green', 'yellow', 'orange', 'red'];
const CONFIDENCES: readonly string[] = ['high', 'medium', 'low'];
const STORED_KEYS = [
  'confidence',
  'score_range',
  'model_version',
  'evidence',
  'insufficient_reason',
  'relative_rate',
  'per_10k_logged_ascents',
  'per_10k_reason',
] as const;

const isFiniteNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;

// Exactly the two legal shapes: a gray colour with a number, or a number with an
// insufficient status, is malformed (the backend schema rejects both too).
const isRiskResult = (body: Record<string, unknown>): boolean => {
  if (body.data_status === 'insufficient_data') {
    return body.risk_score === null && body.color_code === 'gray';
  }
  return (
    body.data_status === 'ok' &&
    isFiniteNumber(body.risk_score) &&
    body.risk_score >= 0 &&
    body.risk_score <= 100 &&
    typeof body.color_code === 'string' &&
    COLOR_CODES.includes(body.color_code)
  );
};

const isInterval = (v: unknown): boolean =>
  isRecord(v) &&
  isFiniteNumber(v.mean) &&
  isFiniteNumber(v.lo) &&
  isFiniteNumber(v.hi) &&
  v.lo >= 0 &&
  v.lo <= v.mean &&
  v.mean <= v.hi;

// The backend serialises absent optional fields as explicit null (no exclude_none), so null
// and undefined both mean "absent" everywhere below.
const isOptionalInterval = (v: unknown): boolean => v == null || isInterval(v);

const isStoredOk = (body: Record<string, unknown>): boolean => {
  const score = body.risk_score as number;
  const range = body.score_range;
  const evidence = body.evidence;
  return (
    typeof body.confidence === 'string' &&
    CONFIDENCES.includes(body.confidence) &&
    Array.isArray(range) &&
    range.length === 2 &&
    isFiniteNumber(range[0]) &&
    isFiniteNumber(range[1]) &&
    range[0] >= 0 &&
    range[0] <= score &&
    score <= range[1] &&
    range[1] <= 100 &&
    typeof body.model_version === 'string' &&
    isRecord(evidence) &&
    isFiniteNumber(evidence.geo) &&
    isFiniteNumber(evidence.similar) &&
    evidence.geo >= 0 &&
    evidence.similar >= 0 &&
    body.insufficient_reason == null &&
    isOptionalInterval(body.relative_rate) &&
    isOptionalInterval(body.per_10k_logged_ascents) &&
    (body.per_10k_reason == null || typeof body.per_10k_reason === 'string')
  );
};

const isStoredInsufficient = (body: Record<string, unknown>): boolean =>
  body.insufficient_reason === 'no_evidence' &&
  typeof body.model_version === 'string' &&
  body.confidence == null &&
  body.score_range == null &&
  body.evidence == null &&
  body.relative_rate == null &&
  isOptionalInterval(body.per_10k_logged_ascents) &&
  (body.per_10k_reason == null || typeof body.per_10k_reason === 'string');

// Any stored-score key present means the body claims the new contract and must meet all of
// it; none present is the old shape. A half-formed body is never read as the old shape.
export const isSafetyResponse = (value: unknown): value is SafetyResponse => {
  if (!isRecord(value)) return false;
  const body = value;
  if (
    typeof body.route_id !== 'number' ||
    typeof body.route_name !== 'string' ||
    typeof body.target_date !== 'string' ||
    !isRiskResult(body)
  ) {
    return false;
  }
  if (STORED_KEYS.every((key) => body[key] == null)) return true;
  return body.data_status === 'ok' ? isStoredOk(body) : isStoredInsufficient(body);
};

export const parseSafety = (value: unknown): SafetyResponse => {
  if (!isSafetyResponse(value)) {
    throw new Error('Malformed safety response from the API.');
  }
  return value;
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

export const SCORE_UNAVAILABLE_MESSAGE = 'Score unavailable for this route.';

export const fetchRouteSafety = async (routeId: number, targetDate: string): Promise<SafetyResponse> => {
  let data: unknown;
  try {
    const response = await api.post(`/mp-routes/${routeId}/safety`, null, {
      params: { target_date: targetDate },
    });
    data = response.data;
  } catch (error) {
    // The stored-score API answers 404 when the active model has no row for the route:
    // the score is unavailable, which is not the same as "no evidence" (gray).
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      throw new Error(SCORE_UNAVAILABLE_MESSAGE);
    }
    throw toReadableError(error);
  }
  return parseSafety(data);
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
