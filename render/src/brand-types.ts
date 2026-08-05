/**
 * KH Clipper — Wave 2 (KH-MGX-001) brand types.
 *
 * The SHAPE of brand.json, exported by `python -m src.export_brand` from the
 * single source of truth, src/brand.py. React never hardcodes a colour, font
 * or timing — everything here is read from the exported file at render time.
 */

export type BrandColours = {
  gold: string;
  darkOlive: string;
  secOlive: string;
  creamWhite: string;
  neutralCream: string;
  lime: string;
};

export type BrandFonts = {
  headingFamily: string;
  headingFile: string; // relative to the repo root, e.g. "assets/fonts/KH-Heading.ttf"
  captionFamily: string;
  captionFile: string;
};

export type BrandCaption = {
  fontSize: number;
  maxWordsPerLine: number;
  outlinePx: number;
  boxOpacity: number;
};

export type BrandAnimation = {
  popMs: number;
  popFromScale: number; // percent, e.g. 80
  restScale: number;
  activeScale: number;
  lineFadeMs: number;
  highlightScale: number;
  captionBands: {
    defaultMarginVPx: number;
    raisedMarginVPx: number;
    lowFaceThreshold: number;
  };
  bannerBands: {
    defaultMarginVPx: number;
    midMarginVPx: number;
    highFaceThreshold: number;
  };
  punchIn: {
    enabled: boolean;
    startScale: number;
    endScale: number;
  };
  quoteCardIntro: {
    enabled: boolean;
    durationSec: number;
    driftPx: number;
  };
  // Wave 2 — KH End Screen: the tail-window CTA overlay's own timing knobs.
  // `enabled` is the global kill-switch (mirrors punchIn's/quoteCardIntro's);
  // the per-clip opt-out is a separate boolean, `KhKineticProps.endScreenCta`.
  endScreen: {
    enabled: boolean;
    windowSec: number;
    staggerMs: number;
  };
  presets: {
    standard: PresetConfig;
    calm: PresetConfig;
  };
};

export type PresetConfig = {
  pop: boolean;
  highlight: boolean;
  punchIn: boolean;
  fadeMs: number;
};

// Wave 2 — KH End Screen: brand.CTA reused verbatim (copy + native-UI target
// pixels), exported by src/export_brand.py from the SAME dict src/cta.py
// (the classic libass CTA) already reads — never a second copy of the copy
// or the tuned target positions. Colours are not duplicated here: the CTA's
// pill/text/accent colours are the same darkOlive/creamWhite/gold already in
// BrandColours, so the render layer reads those directly.
export type BrandCta = {
  copy: {
    subscribeSoft: string;
    subscribe: string;
    fullEpisode: string;
    related: string;
    handle: string;
  };
  shortsTargets: {
    subscribeBtn: [number, number];
    channelProfile: [number, number];
    relatedLink: [number, number];
  };
  pillOpacity: number;
  fontSize: number;
};

export type Brand = {
  colours: BrandColours;
  fonts: BrandFonts;
  caption: BrandCaption;
  animation: BrandAnimation;
  cta: BrandCta;
};

export type FaceBand = {
  top: number; // normalised 0-1
  bottom: number; // normalised 0-1
} | null;

export type WordTiming = {
  text: string;
  start: number; // clip-relative seconds
  end: number;
};

export type KhKineticProps = {
  videoFileName: string; // resolved via staticFile() inside the composition
  words: WordTiming[];
  highlightWord: string | null;
  banner: string | null;
  safety: "ok" | "review" | string;
  faceband: FaceBand;
  brand: Brand;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number; // the VIDEO's own duration — never includes the intro
  quoteCardIntro: boolean; // opt-in per render; off by default (see Root.tsx defaultProps)
  // Wave 2 — KH End Screen. `variant` picks which CTA flavour renders (only
  // matters when endScreenCta is on): "shorts" points gold arrows at
  // YouTube's native Shorts UI buttons; "universal" shows branded text only
  // (the @handle), no arrows, for Reels/TikTok. Defaults to "shorts" so a
  // caller that never sets it still gets a sensible render.
  variant?: "shorts" | "universal";
  // Default ON (parity with classic's own always-on CTA, src/cta.py) — the
  // per-clip opt-out src.kinetic.finish(end_screen_cta=False) threads
  // through to here. A no-op past the end of a genuinely tiny clip (the
  // window clamps to the clip's own duration).
  endScreenCta?: boolean;
};
