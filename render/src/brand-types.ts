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

export type Brand = {
  colours: BrandColours;
  fonts: BrandFonts;
  caption: BrandCaption;
  animation: BrandAnimation;
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
};
