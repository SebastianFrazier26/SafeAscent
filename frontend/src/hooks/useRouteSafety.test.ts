import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchRouteSafety, type SafetyResponse } from '../services/api';
import { useRouteSafety } from './useRouteSafety';

vi.mock('../services/api', () => ({ fetchRouteSafety: vi.fn() }));

const OK: SafetyResponse = {
  route_id: 42,
  route_name: 'Test Route',
  target_date: '2026-09-27',
  risk_score: 42.3,
  color_code: 'yellow',
};

beforeEach(() => vi.mocked(fetchRouteSafety).mockReset());

describe('useRouteSafety', () => {
  it('is null when no route is selected and does not fetch', () => {
    const { result } = renderHook(() => useRouteSafety(null, '2026-09-27'));
    expect(result.current.state).toBeNull();
    expect(fetchRouteSafety).not.toHaveBeenCalled();
  });

  it('goes loading → ok', async () => {
    vi.mocked(fetchRouteSafety).mockResolvedValue(OK);
    const { result } = renderHook(() => useRouteSafety(42, '2026-09-27'));
    expect(result.current.state).toEqual({ status: 'loading' });
    await waitFor(() => expect(result.current.state).toEqual({ status: 'ok', data: OK }));
    expect(fetchRouteSafety).toHaveBeenCalledWith(42, '2026-09-27');
  });

  it('goes loading → error with the message, and retry refetches', async () => {
    vi.mocked(fetchRouteSafety).mockRejectedValueOnce(new Error('Network down')).mockResolvedValueOnce(OK);
    const { result } = renderHook(() => useRouteSafety(42, '2026-09-27'));
    await waitFor(() => expect(result.current.state).toEqual({ status: 'error', message: 'Network down' }));
    act(() => result.current.retry());
    expect(result.current.state).toEqual({ status: 'loading' });
    await waitFor(() => expect(result.current.state).toEqual({ status: 'ok', data: OK }));
    expect(fetchRouteSafety).toHaveBeenCalledTimes(2);
  });

  it('ignores a stale response after the route changes', async () => {
    let resolveFirst: (value: SafetyResponse) => void = () => {};
    vi.mocked(fetchRouteSafety)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({ ...OK, route_id: 7, risk_score: 10 });
    const { result, rerender } = renderHook(({ id }) => useRouteSafety(id, '2026-09-27'), {
      initialProps: { id: 42 },
    });
    rerender({ id: 7 });
    await waitFor(() => expect(result.current.state).toMatchObject({ status: 'ok', data: { route_id: 7 } }));
    act(() => resolveFirst(OK));
    expect(result.current.state).toMatchObject({ status: 'ok', data: { route_id: 7 } });
  });
});
