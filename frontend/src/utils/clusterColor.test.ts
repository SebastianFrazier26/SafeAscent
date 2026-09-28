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

const expressionHex = (scored: number, total: number, sum: number): unknown =>
  evaluate(clusterColorExpression(RISK_COLOR_HEX, NO_RISK_HEX), {
    risk_score_count: scored,
    point_count: total,
    risk_score_sum: sum,
  });

const codeHex = (scored: number, total: number, sum: number): string => {
  const code = clusterColorCode(scored, total, sum);
  return code === 'gray' ? NO_RISK_HEX : RISK_COLOR_HEX[code];
};

describe('clusterColorCode', () => {
  it('is gray when 49 of 50 routes are insufficient and the one scored route is low', () => {
    expect(clusterColorCode(1, 50, 10)).toBe('gray');
  });

  it('uses the scored mean when exactly half the routes are scored', () => {
    expect(clusterColorCode(2, 4, 110)).toBe('orange');
    expect(clusterColorCode(25, 50, 25 * 10)).toBe('green');
  });

  it('uses the scored mean when every route is scored', () => {
    expect(clusterColorCode(3, 3, 30 + 50 + 70)).toBe('orange');
    expect(clusterColorCode(2, 2, 160)).toBe('red');
  });

  it('is gray with no scored routes', () => {
    expect(clusterColorCode(0, 12, 0)).toBe('gray');
    expect(clusterColorCode(0, 0, 0)).toBe('gray');
  });

  it('is gray just under half scored', () => {
    expect(clusterColorCode(24, 49, 24 * 80)).toBe('gray');
  });
});

describe('clusterColorExpression', () => {
  it.each([
    [1, 50, 10],
    [2, 4, 110],
    [25, 50, 250],
    [24, 49, 24 * 80],
    [3, 3, 150],
    [2, 2, 160],
    [0, 12, 0],
    [4, 4, 100],
    [4, 4, 99.6],
    [5, 7, 5 * 75],
  ])('matches clusterColorCode for scored=%i total=%i sum=%f', (scored, total, sum) => {
    expect(expressionHex(scored, total, sum)).toBe(codeHex(scored, total, sum));
  });
});

describe('MapView clusters', () => {
  const mapView = import.meta.glob('../components/MapView.jsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

  it('colour fill and label with clusterColorExpression and keep the scored-only count', () => {
    const text = (mapView['../components/MapView.jsx'] ?? '').replace(/\s+/g, '');
    expect(text).toContain("'circle-color':clusterColorExpression(RISK_COLOR_HEX,NO_RISK_HEX)");
    expect(text).toContain("'text-color':clusterColorExpression(RISK_TEXT_ON_HEX,NO_RISK_TEXT_HEX)");
    expect(text).toContain("risk_score_count:['+',['case',['==',['typeof',['get','risk_score']],'number'],1,0]]");
  });
});
