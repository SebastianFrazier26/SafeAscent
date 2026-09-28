import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import api, { fetchRouteSafety, isSafetyResponse } from './api';

const OK = {
  route_id: 42,
  route_name: 'Test Route',
  target_date: '2026-09-27',
  risk_score: 42.3,
  color_code: 'yellow',
};

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
