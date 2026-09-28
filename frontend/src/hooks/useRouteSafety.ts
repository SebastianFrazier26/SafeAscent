import { useCallback, useEffect, useState } from 'react';
import { fetchRouteSafety, type SafetyResponse } from '../services/api';

export type SafetyState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ok'; data: SafetyResponse };

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
        if (!ignore) setSettled({ key: requestKey, state: { status: 'ok', data } });
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
