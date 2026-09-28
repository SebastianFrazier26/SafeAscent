import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import api, { fetchRouteSafety, isPredictionResponse, isSafetyResponse, predictRouteSafety } from './api';

const OK = {
  route_id: 42,
  route_name: 'Test Route',
  target_date: '2026-09-27',
  risk_score: 42.3,
  color_code: 'yellow',
  data_status: 'ok',
};

// Owner decision 2026-09-28: no contributing evidence -> null score, gray, explicit status.
const INSUFFICIENT = { ...OK, risk_score: null, color_code: 'gray', data_status: 'insufficient_data' };

afterEach(() => vi.restoreAllMocks());

describe('isSafetyResponse', () => {
  it('accepts a well-formed payload', () => {
    expect(isSafetyResponse(OK)).toBe(true);
  });

  it('rejects a payload without a finite risk_score', () => {
    expect(isSafetyResponse({ ...OK, risk_score: null })).toBe(false);
    expect(isSafetyResponse({ error: 'boom' })).toBe(false);
  });

  it('accepts the 0 and 100 bounds', () => {
    expect(isSafetyResponse({ ...OK, risk_score: 0 })).toBe(true);
    expect(isSafetyResponse({ ...OK, risk_score: 100 })).toBe(true);
  });

  it('rejects a colour outside the four-band enum, e.g. the backend "gray" sentinel', () => {
    expect(isSafetyResponse({ ...OK, color_code: 'gray' })).toBe(false);
  });

  it('accepts the insufficient-data shape: null score + gray + explicit status', () => {
    expect(isSafetyResponse(INSUFFICIENT)).toBe(true);
  });

  it.each([
    ['gray with a number', { ...INSUFFICIENT, risk_score: 12 }],
    ['gray with a zero', { ...INSUFFICIENT, risk_score: 0 }],
    ['insufficient with a band colour', { ...INSUFFICIENT, color_code: 'green' }],
    ['ok with a null score', { ...OK, risk_score: null }],
    ['ok with gray', { ...OK, color_code: 'gray' }],
    ['missing data_status', { ...OK, data_status: undefined }],
    ['unknown data_status', { ...OK, data_status: 'maybe' }],
    ['insufficient with a missing score', { ...INSUFFICIENT, risk_score: undefined }],
  ])('rejects %s', (_label, body) => {
    expect(isSafetyResponse(body)).toBe(false);
  });
});

const PREDICTION_OK = {
  risk_score: 35.5,
  color_code: 'yellow',
  data_status: 'ok',
  num_contributing_accidents: 12,
  top_contributing_accidents: [],
  metadata: {},
};
const PREDICTION_INSUFFICIENT = {
  ...PREDICTION_OK,
  risk_score: null,
  color_code: 'gray',
  data_status: 'insufficient_data',
  num_contributing_accidents: 0,
};

describe('isPredictionResponse', () => {
  it('accepts ok and insufficient shapes', () => {
    expect(isPredictionResponse(PREDICTION_OK)).toBe(true);
    expect(isPredictionResponse(PREDICTION_INSUFFICIENT)).toBe(true);
  });

  it.each([
    ['gray with a number', { ...PREDICTION_INSUFFICIENT, risk_score: 3 }],
    ['ok with null', { ...PREDICTION_OK, risk_score: null }],
    ['out of range', { ...PREDICTION_OK, risk_score: 101 }],
    ['missing status', { ...PREDICTION_OK, data_status: undefined }],
    ['missing accidents list', { ...PREDICTION_OK, top_contributing_accidents: undefined }],
  ])('rejects %s', (_label, body) => {
    expect(isPredictionResponse(body)).toBe(false);
  });
});

describe('predictRouteSafety', () => {
  const params = { latitude: 40, longitude: -105, route_type: 'trad', planned_date: '2026-09-28' };

  it('returns a validated insufficient-data body', async () => {
    vi.spyOn(api, 'post').mockResolvedValue({ data: PREDICTION_INSUFFICIENT } as never);
    await expect(predictRouteSafety(params)).resolves.toEqual(PREDICTION_INSUFFICIENT);
  });

  it('rejects a malformed body instead of returning it', async () => {
    vi.spyOn(api, 'post').mockResolvedValue({ data: { ...PREDICTION_INSUFFICIENT, risk_score: 0 } } as never);
    await expect(predictRouteSafety(params)).rejects.toThrow('Malformed prediction response');
  });
});

describe('fetchRouteSafety', () => {
  it('posts to the safety endpoint and returns the validated body', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: OK } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).resolves.toEqual(OK);
    expect(post).toHaveBeenCalledWith('/mp-routes/42/safety', null, {
      params: { target_date: '2026-09-27', bypass_cache: true },
    });
  });

  it('rejects a malformed body instead of returning it', async () => {
    vi.spyOn(api, 'post').mockResolvedValue({ data: { ...OK, risk_score: undefined } } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Malformed safety response');
  });

  it('returns an insufficient-data body as data, not an error', async () => {
    vi.spyOn(api, 'post').mockResolvedValue({ data: INSUFFICIENT } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).resolves.toEqual(INSUFFICIENT);
  });

  it.each([-1, 100.01, 150])('rejects out-of-range risk_score %s', async (risk_score) => {
    vi.spyOn(api, 'post').mockResolvedValue({ data: { ...OK, risk_score } } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Malformed safety response');
  });

  it('turns a network failure into a readable error', async () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK', { headers: new AxiosHeaders() });
    vi.spyOn(api, 'post').mockRejectedValue(error);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Cannot connect to SafeAscent API');
  });
});
