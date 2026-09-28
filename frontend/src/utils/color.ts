/** Colour helpers for `#rrggbb` hexes. */

const channels = (hex: string): [number, number, number] => {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) throw new Error(`expected #rrggbb, got ${hex}`);
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
};

const toHex = (rgb: number[]): string =>
  `#${rgb.map((c) => Math.round(c).toString(16).padStart(2, '0')).join('')}`;

// WCAG 2.x relative luminance.
const luminance = (hex: string): number => {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

export const contrastRatio = (a: string, b: string): number => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// Opaque candidates so the computed ratio is the rendered ratio (a translucent text colour's
// contrast depends on what it is composited over).
const TEXT_CANDIDATES = ['#000000', '#ffffff'];

/** Whichever of black/white has the higher WCAG contrast on `bg`. */
export const readableTextOn = (bg: string): string =>
  TEXT_CANDIDATES.reduce((best, c) => (contrastRatio(c, bg) > contrastRatio(best, bg) ? c : best));

export const hexToRgba = (hex: string, alpha: number): string =>
  `rgba(${channels(hex).join(', ')}, ${alpha})`;

/** Channel-wise mix of the sRGB values: t=0 is `a`, t=1 is `b`. */
export const mixHex = (a: string, b: string, t: number): string => {
  const ca = channels(a);
  const cb = channels(b);
  return toHex(ca.map((c, i) => c + (cb[i] - c) * t));
};
