import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import api, { fetchNearbyAccidents, fetchRouteSafety, isSafetyResponse } from './api';

const OK = {
  route_id: 42,
  route_name: 'Test Route',
  target_date: '2026-09-27',
  risk_score: 42.3,
  color_code: 'yellow',
  data_status: 'ok',
};

// Owner decision 2026-09-28: too little evidence -> null score, gray, explicit status.
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

  it('reads a 404 as score unavailable, not as insufficient data', async () => {
    const error = new AxiosError('Not Found', 'ERR_BAD_REQUEST', { headers: new AxiosHeaders() }, null, {
      status: 404, statusText: 'Not Found', data: { detail: 'score unavailable' }, headers: {}, config: { headers: new AxiosHeaders() },
    });
    vi.spyOn(api, 'post').mockRejectedValue(error);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Score unavailable for this route.');
  });

  it('tolerates the additive stored-score fields', async () => {
    const stored = { ...OK, confidence: 'low', score_range: [30, 55], model_version: 'eb_v1-x', evidence: { geo: 0.4, similar: 2 } };
    vi.spyOn(api, 'post').mockResolvedValue({ data: stored } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).resolves.toEqual(stored);
  });

  it('turns a network failure into a readable error', async () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK', { headers: new AxiosHeaders() });
    vi.spyOn(api, 'post').mockRejectedValue(error);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Cannot connect to SafeAscent API');
  });
});

describe('fetchNearbyAccidents', () => {
  it('sends the lat/lon/radius_km names the API reads and returns the list', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: { total: 1, data: [{ accident_id: 7 }] } } as never);
    await expect(fetchNearbyAccidents(40.1, -105.2, 10)).resolves.toEqual([{ accident_id: 7 }]);
    expect(get).toHaveBeenCalledWith('/accidents', {
      params: { lat: 40.1, lon: -105.2, radius_km: 10, limit: 100 },
    });
  });

  it('throws on a failed request instead of reporting no accidents', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('down'));
    await expect(fetchNearbyAccidents(40, -105)).rejects.toThrow();
  });

  it('throws on a malformed body', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ data: [{ accident_id: 7 }] } as never);
    await expect(fetchNearbyAccidents(40, -105)).rejects.toThrow('Malformed accident list');
  });
});
