/**
 * KH Clipper — Wave 2 (KH-MGX-001): KH Audiogram v2 types.
 *
 * The SHAPE of brand.json's `audiogramV2` key (exported by
 * `python -m src.export_brand` from src/brand.py's AUDIOGRAM_V2 block) plus
 * the per-render props the Python bridge (src/audiogram_v2.py) computes and
 * hands to render-cli.mjs. Kept separate from brand-types.ts's `Brand`
 * (KH Kinetic's own shape) so the two templates' contracts can't drift into
 * each other by accident.
 */

export type AudioFont = { family: string; file: string };

export type AudiogramFormatLayout = {
  padX: number;
  padY: number;
  logoH: number;
  eyebrowSize: number;
  captionSize: number;
  waveformH: number;
  barW: number;
  barGap: number;
  // Exactly one of these is set, matching src/brand.py's AUDIOGRAM_V2["layout"].
  captionMaxWFrac?: number; // fraction of the full frame width (landscape)
  captionMaxWCqw?: number; // cqw units, i.e. percent of min(width,height) (tall/square)
};

export type AudiogramBrand = {
  fonts: {
    headingBold: AudioFont;
    headingXBold: AudioFont;
    body: AudioFont;
    mono: AudioFont;
  };
  bars: {
    count: number;
    minScale: number;
    entranceMs: number;
    entranceStaggerMs: number;
    springDamping: number;
    springMass: number;
    calmFadeMs: number;
  };
  progress: { fillGlowMs: number };
  captionTransition: { fadeMs: number; calmFadeMs: number };
  presets: {
    standard: { barEntranceSpring: boolean; progressGlow: boolean };
    calm: { barEntranceSpring: boolean; progressGlow: boolean };
  };
  layout: {
    wide: AudiogramFormatLayout;
    tall: AudiogramFormatLayout;
    square: AudiogramFormatLayout;
    barRadius: number;
    centreGap: number;
    progressH: number;
    progressGap: number;
  };
};

// Per-series palette, resolved server-side by src/audiogram.py's
// resolve_brand() (same mechanism the Pillow version already uses) — kept
// OUT of brand.json since it's per-request/per-series, not global brand.
export type AudiogramPalette = {
  bg: string;
  ink: string;
  accent: string;
  logoFileName: string; // resolved by the Python bridge, staged by render-cli.mjs
};

export type AudiogramSeedBar = { h: number; dur: number; delay: number };

export type AudiogramTimedLine = { start: number; end: number; text: string };

export type AudiogramV2Props = {
  videoFileName: string; // staged clip — audio track only, no video composited
  title: string | null;
  guestName: string | null;
  eyebrow: string;
  epLabel: string | null;
  // Static caption (shown throughout) OR timed caption chunks — never both.
  caption: string | null;
  timedLines: AudiogramTimedLine[] | null;
  // Real per-frame audio envelope, [frame][bar] in 0..1. null -> seeded
  // oscillation fallback (seedBars), matching src/audiogram.py's degrade path.
  amps: number[][] | null;
  seedBars: AudiogramSeedBar[];
  palette: AudiogramPalette;
  safety: "ok" | "review" | string;
  brand: AudiogramBrand;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
};
