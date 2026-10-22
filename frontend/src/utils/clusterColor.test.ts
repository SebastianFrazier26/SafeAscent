import { describe, expect, it } from 'vitest';
import { NO_RISK_HEX, RISK_COLOR_HEX } from './riskUtils';
import { clusterColorCode, clusterColorExpression } from './clusterColor';

type Props = Record<string, number>;

// Just enough of the Mapbox expression language to run clusterColorExpression, so the
// style expression and clusterColorCode are checked against each other, not only by reading.
const evaluate = (expr: unknown, props: Props): unknown => {
  if (!Array.isArray(expr)) return expr;
  const [op, ...args] = expr as [string, ...unknown[]];
  const num = (e: unknown) => evaluate(e, props) as number;
  switch (op) {
    case 'get':
      return props[args[0] as string];
    case '>':
      return num(args[0]) > num(args[1]);
    case '>=':
      return num(args[0]) >= num(args[1]);
    case '/':
      return num(args[0]) / num(args[1]);
    case '-':
      return num(args[0]) - num(args[1]);
    case 'all':
      return args.every((a) => evaluate(a, props) === true);
    case 'case': {
      for (let i = 0; i + 1 < args.length; i += 2) {
        if (evaluate(args[i], props) === true) return evaluate(args[i + 1], props);
      }
      return evaluate(args[args.length - 1], props);
    }
    case 'step': {
      const input = num(args[0]);
      let out = evaluate(args[1], props);
      for (let i = 2; i + 1 < args.length; i += 2) {
        if (input >= num(args[i])) out = evaluate(args[i + 1], props);
      }
      return out;
    }
    default:
      throw new Error(`evaluator does not support ${op}`);
  }
};

const RING = 'ring';

const expressionHex = (scored: number, insufficient: number, total: number, sum: number): unknown =>
  evaluate(clusterColorExpression(RISK_COLOR_HEX, NO_RISK_HEX, RING), {
    risk_score_count: scored,
    insufficient_count: insufficient,
    point_count: total,
    risk_score_sum: sum,
  });

const codeHex = (scored: number, insufficient: number, total: number, sum: number): string => {
  const code = clusterColorCode(scored, insufficient, total, sum);
  if (code === 'gray') return NO_RISK_HEX;
  if (code === 'none') return RING;
  return RISK_COLOR_HEX[code];
};

describe('clusterColorCode', () => {
  it('is gray when 49 of 50 routes are insufficient and the one scored route is low', () => {
    expect(clusterColorCode(1, 49, 50, 10)).toBe('gray');
  });

  it('uses the scored mean when exactly half the routes are scored', () => {
    expect(clusterColorCode(2, 0, 4, 110)).toBe('orange');
    expect(clusterColorCode(25, 25, 50, 25 * 10)).toBe('green');
  });

  it('uses the scored mean when every route is scored', () => {
    expect(clusterColorCode(3, 0, 3, 30 + 50 + 70)).toBe('orange');
    expect(clusterColorCode(2, 0, 2, 160)).toBe('red');
  });

  it('is gray with no scored routes when all are insufficient', () => {
    expect(clusterColorCode(0, 12, 12, 0)).toBe('gray');
  });

  it('is gray just under half scored when the rest are insufficient', () => {
    expect(clusterColorCode(24, 25, 49, 24 * 80)).toBe('gray');
  });

  it('is never gray when unavailable routes outnumber insufficient ones', () => {
    expect(clusterColorCode(0, 0, 12, 0)).toBe('none');
    expect(clusterColorCode(1, 4, 50, 10)).toBe('none');
    expect(clusterColorCode(24, 12, 49, 24 * 80)).toBe('none');
  });

  it('is gray on a tie between insufficient and unavailable', () => {
    expect(clusterColorCode(0, 6, 12, 0)).toBe('gray');
    expect(clusterColorCode(0, 0, 0, 0)).toBe('gray');
  });
});

describe('clusterColorExpression', () => {
  it.each([
    [1, 49, 50, 10],
    [1, 4, 50, 10],
    [2, 0, 4, 110],
    [25, 25, 50, 250],
    [24, 25, 49, 24 * 80],
    [24, 12, 49, 24 * 80],
    [3, 0, 3, 150],
    [2, 0, 2, 160],
    [0, 12, 12, 0],
    [0, 0, 12, 0],
    [0, 6, 12, 0],
    [0, 5, 12, 0],
    [4, 0, 4, 100],
    [4, 0, 4, 99.6],
    [5, 0, 7, 5 * 75],
  ])('matches clusterColorCode for scored=%i insufficient=%i total=%i sum=%f', (scored, insufficient, total, sum) => {
    expect(expressionHex(scored, insufficient, total, sum)).toBe(codeHex(scored, insufficient, total, sum));
  });

  it('a mostly-unavailable cluster is the ring, never the no-evidence gray', () => {
    expect(expressionHex(0, 1, 12, 0)).toBe(RING);
    expect(expressionHex(0, 1, 12, 0)).not.toBe(NO_RISK_HEX);
  });
});

describe('MapView clusters', () => {
  const mapView = import.meta.glob('../components/MapView.jsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

  it('colour fill and label with clusterColorExpression and count scored and insufficient routes', () => {
    const text = (mapView['../components/MapView.jsx'] ?? '').replace(/\s+/g, '');
    expect(text).toContain("'circle-color':clusterColorExpression(RISK_COLOR_HEX,NO_RISK_HEX,UNAVAILABLE_FILL)");
    expect(text).toContain("'text-color':clusterColorExpression(RISK_TEXT_ON_HEX,NO_RISK_TEXT_HEX,CLUSTER_RING_TEXT_HEX)");
    expect(text).toContain("risk_score_count:['+',['case',['==',['typeof',['get','risk_score']],'number'],1,0]]");
    expect(text).toContain("insufficient_count:['+',['case',['==',['get','data_status'],'insufficient_data'],1,0]]");
  });
});
