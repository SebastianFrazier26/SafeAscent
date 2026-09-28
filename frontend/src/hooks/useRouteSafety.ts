import { useCallback, useEffect, useState } from 'react';
import { fetchRouteSafety, type SafetyResponse } from '../services/api';

type Scored = Extract<SafetyResponse, { data_status: 'ok' }>;
type Insufficient = Extract<SafetyResponse, { data_status: 'insufficient_data' }>;

// 'insufficient' is neither ok nor error: the request worked, the route just has no
// evidence yet (owner decision 2026-09-28), so there is nothing to retry.
export type SafetyState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; data: Scored }
  | { status: 'insufficient'; data: Insufficient };

interface Settled {
  key: string;
  state: SafetyState;
}

export function useRouteSafety(
  routeId: number | null,
  targetDate: string,
): { state: SafetyState | null; retry: () => void } {
  const [attempt, setAttempt] = useState(0);
  const [settled, setSettled] = useState<Settled | null>(null);
  const key = routeId === null ? null : `${routeId}|${targetDate}|${attempt}`;

  useEffect(() => {
    if (routeId === null) return;
    const requestKey = `${routeId}|${targetDate}|${attempt}`;
    let ignore = false;
    fetchRouteSafety(routeId, targetDate).then(
      (data) => {
        if (ignore) return;
        const state: SafetyState =
          data.data_status === 'insufficient_data' ? { status: 'insufficient', data } : { status: 'ok', data };
        setSettled({ key: requestKey, state });
      },
      (error: unknown) => {
        if (!ignore) {
          const message = error instanceof Error ? error.message : 'Unknown error';
          setSettled({ key: requestKey, state: { status: 'error', message } });
        }
      },
    );
    return () => {
      ignore = true;
    };
  }, [routeId, targetDate, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  // Loading is derived from "no settled result for this request key" rather than set
  // synchronously in the effect, so a previous route's score can never flash.
  const state: SafetyState | null =
    key === null ? null : settled?.key === key ? settled.state : { status: 'loading' };
  return { state, retry };
}
