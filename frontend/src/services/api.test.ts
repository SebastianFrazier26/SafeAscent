import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import api, {
  fetchNearbyAccidents,
  fetchRouteSafety,
  isInsufficient,
  isSafetyResponse,
  isScored,
  isStoredScore,
  parseSafety,
  SCORE_UNAVAILABLE_MESSAGE,
} from './api';

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

const STORED = {
  ...OK,
  confidence: 'low',
  score_range: [30, 55],
  model_version: 'eb_v1-2026-10-20',
  evidence: { geo: 0.4, similar: 2 },
  relative_rate: { mean: 1.3, lo: 0.8, hi: 2.1 },
  per_10k_logged_ascents: null,
  per_10k_reason: 'ticks_incomplete',
};

const STORED_INSUFFICIENT = {
  ...INSUFFICIENT,
  model_version: 'eb_v1-2026-10-20',
  insufficient_reason: 'no_evidence',
};

afterEach(() => vi.restoreAllMocks());

const notFound = () =>
  new AxiosError('Not Found', 'ERR_BAD_REQUEST', { headers: new AxiosHeaders() }, null, {
    status: 404, statusText: 'Not Found', data: { detail: 'score unavailable' }, headers: {}, config: { headers: new AxiosHeaders() },
  });

// Exact key set of backend StoredScoreFields: the API sends absent fields as explicit null.
const WIRE_OK = {
  ...OK,
  confidence: 'medium',
  score_range: [30.1, 61.0],
  model_version: 'eb_v1-2026-10-20',
  evidence: { geo: 1.4, similar: 6.2 },
  insufficient_reason: null,
  relative_rate: null,
  per_10k_logged_ascents: null,
  per_10k_reason: null,
};
const WIRE_INSUFFICIENT = {
  ...INSUFFICIENT,
  confidence: null,
  score_range: null,
  model_version: 'eb_v1-2026-10-20',
  evidence: null,
  insufficient_reason: 'no_evidence',
  relative_rate: null,
  per_10k_logged_ascents: null,
  per_10k_reason: null,
};

describe('parseSafety against the backend wire shape (explicit nulls)', () => {
  it('parses an ok body whose nullable fields are null', () => {
    const parsed = parseSafety(WIRE_OK);
    expect(isStoredScore(parsed)).toBe(true);
    expect(parsed.risk_score).toBe(42.3);
  });

  it('parses an insufficient body whose ok-only fields are null', () => {
    const parsed = parseSafety(WIRE_INSUFFICIENT);
    expect(isInsufficient(parsed)).toBe(true);
    expect(parsed.risk_score).toBeNull();
  });

  it('parses an insufficient body that still carries a per-10k interval', () => {
    const body = { ...WIRE_INSUFFICIENT, per_10k_logged_ascents: { mean: 1, lo: 0.5, hi: 2 }, per_10k_reason: null };
    expect(isInsufficient(parseSafety(body))).toBe(true);
  });

  it('reads an all-null body as the old shape', () => {
    const allNull = { ...OK, confidence: null, score_range: null, model_version: null, evidence: null, insufficient_reason: null };
    expect(isStoredScore(parseSafety(allNull))).toBe(false);
  });

  it('still throws when a null-padded ok body lacks its range', () => {
    expect(() => parseSafety({ ...WIRE_OK, score_range: null })).toThrow('Malformed safety response');
  });

  it('still throws on a null-padded insufficient body without a reason', () => {
    expect(() => parseSafety({ ...WIRE_INSUFFICIENT, insufficient_reason: null })).toThrow('Malformed safety response');
  });
});

describe('parseSafety', () => {
  it('parses a low-confidence payload and keeps the number and its range', () => {
    const parsed = parseSafety(STORED);
    expect(parsed.risk_score).toBe(42.3);
    expect(isStoredScore(parsed) && parsed.score_range).toEqual([30, 55]);
  });

  it('keeps a null relative_rate on an ok score', () => {
    expect(parseSafety({ ...STORED, relative_rate: null }).data_status).toBe('ok');
  });

  it('parses a stored insufficient payload with a null score', () => {
    const parsed = parseSafety(STORED_INSUFFICIENT);
    expect(parsed.risk_score).toBeNull();
    expect(parsed.color_code).toBe('gray');
  });

  it('still accepts the old shape with no stored-score fields', () => {
    expect(parseSafety(OK).risk_score).toBe(42.3);
    expect(parseSafety(INSUFFICIENT).risk_score).toBeNull();
  });

  it.each([
    ['range above the score', { ...STORED, score_range: [50, 60] }],
    ['range below the score', { ...STORED, score_range: [10, 40] }],
    ['range outside 0-100', { ...STORED, score_range: [30, 101] }],
    ['inverted range', { ...STORED, score_range: [55, 30] }],
    ['missing confidence', { ...STORED, confidence: undefined }],
    ['missing score_range', { ...STORED, score_range: undefined }],
    ['missing evidence', { ...STORED, evidence: undefined }],
    ['missing model_version', { ...STORED, model_version: undefined }],
    ['unknown confidence', { ...STORED, confidence: 'certain' }],
    ['non-finite range bound', { ...STORED, score_range: [30, null] }],
    ['non-finite evidence', { ...STORED, evidence: { geo: null, similar: 2 } }],
    ['negative evidence', { ...STORED, evidence: { geo: -1, similar: 2 } }],
    ['unordered rate interval', { ...STORED, relative_rate: { mean: 3, lo: 0.8, hi: 2.1 } }],
    ['non-numeric rate', { ...STORED, relative_rate: { mean: 'x', lo: 0.8, hi: 2.1 } }],
    ['insufficient without a reason', { ...STORED_INSUFFICIENT, insufficient_reason: undefined }],
    ['insufficient with an unknown reason', { ...STORED_INSUFFICIENT, insufficient_reason: 'because' }],
    ['insufficient carrying a confidence', { ...STORED_INSUFFICIENT, confidence: 'low' }],
    ['insufficient carrying a range', { ...STORED_INSUFFICIENT, score_range: [0, 10] }],
    ['ok carrying an insufficient_reason', { ...STORED, insufficient_reason: 'no_evidence' }],
  ])('throws on %s', (_label, body) => {
    expect(() => parseSafety(body)).toThrow('Malformed safety response');
  });
});

describe('score guards discriminate on data_status', () => {
  it('treats an insufficient result as insufficient and never as scored', () => {
    const parsed = parseSafety(STORED_INSUFFICIENT);
    expect(isInsufficient(parsed)).toBe(true);
    expect(isScored(parsed)).toBe(false);
    expect(isStoredScore(parsed)).toBe(false);
  });

  it('treats an ok result as scored', () => {
    const parsed = parseSafety(STORED);
    expect(isScored(parsed)).toBe(true);
    expect(isInsufficient(parsed)).toBe(false);
    expect(isStoredScore(parsed)).toBe(true);
  });

  it('does not call a legacy ok result a stored score', () => {
    const parsed = parseSafety(OK);
    expect(isScored(parsed)).toBe(true);
    expect(isStoredScore(parsed)).toBe(false);
  });
});

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
      params: { target_date: '2026-09-27' },
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

  it('reads a 404 as score unavailable, never as a resolved score', async () => {
    vi.spyOn(api, 'post').mockRejectedValue(notFound());
    const result = await fetchRouteSafety(42, '2026-09-27').then(
      (value) => ({ resolved: value }),
      (error: unknown) => ({ rejected: error }),
    );
    expect(result).not.toHaveProperty('resolved');
    expect((result as { rejected: Error }).rejected.message).toBe(SCORE_UNAVAILABLE_MESSAGE);
  });

  it('rejects a null body rather than reading it as a score', async () => {
    vi.spyOn(api, 'post').mockResolvedValue({ data: null } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Malformed safety response');
  });

  it('rejects a stored payload whose range does not bracket the score', async () => {
    vi.spyOn(api, 'post').mockResolvedValue({ data: { ...STORED, score_range: [50, 60] } } as never);
    await expect(fetchRouteSafety(42, '2026-09-27')).rejects.toThrow('Malformed safety response');
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
