/**
 * KH Clipper — Wave 2 (KH-MGX-001) Remotion entry point.
 *
 * Two compositions: KHKinetic (Shorts captions) and KHAudiogramV2 (the
 * audiogram follow-up template). calculateMetadata resolves the real
 * duration/fps/frame from the input props (render-cli.mjs already knows
 * these from ffprobe, same as Wave 1's caption.py `_duration()`/`_fps()`) so
 * Remotion never has to probe the video itself. When KHKinetic's Quote Card
 * intro is on (`props.quoteCardIntro`), the TOTAL composition length is the
 * video's own duration PLUS the intro card's — `getIntroFrames()` (from
 * KHKinetic.tsx) is the one place that math lives, so this can never drift
 * from what the component itself actually lays out. The End Screen CTA
 * overlay (`props.endScreenCta`, default on) needs NO equivalent change
 * here — it occupies the TAIL of the video's own existing duration rather
 * than extending it, so `durationInFrames` is untouched by it either way.
 */
import React from "react";
import { Composition, registerRoot } from "remotion";
import { KHKinetic, getIntroFrames } from "./KHKinetic";
import { KHAudiogramV2 } from "./KHAudiogramV2";
import type { KhKineticProps } from "./brand-types";
import type { AudiogramV2Props } from "./audiogram-types";

const DEFAULT_PROPS: KhKineticProps = {
  videoFileName: "input.mp4",
  words: [],
  highlightWord: null,
  banner: null,
  safety: "ok",
  faceband: null,
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 150,
  quoteCardIntro: false,
  variant: "shorts",
  endScreenCta: true,
  brand: {
    colours: {
      gold: "#ED9A1F",
      darkOlive: "#2D2F22",
      secOlive: "#424530",
      creamWhite: "#FFF9ED",
      neutralCream: "#FFEFCD",
      lime: "#F0FFA3",
    },
    fonts: {
      headingFamily: "KH Heading",
      headingFile: "assets/fonts/KH-Heading.ttf",
      captionFamily: "KH Caption",
      captionFile: "assets/fonts/KH-Caption.ttf",
    },
    caption: { fontSize: 96, maxWordsPerLine: 4, outlinePx: 4, boxOpacity: 0.8 },
    animation: {
      popMs: 120,
      popFromScale: 80,
      restScale: 100,
      activeScale: 108,
      lineFadeMs: 60,
      highlightScale: 115,
      captionBands: { defaultMarginVPx: 380, raisedMarginVPx: 620, lowFaceThreshold: 0.62 },
      bannerBands: { defaultMarginVPx: 360, midMarginVPx: 520, highFaceThreshold: 0.2 },
      punchIn: { enabled: true, startScale: 1.0, endScale: 1.04 },
      quoteCardIntro: { enabled: true, durationSec: 1.5, driftPx: 24 },
      endScreen: { enabled: true, windowSec: 3.0, staggerMs: 500 },
      presets: {
        standard: { pop: true, highlight: true, punchIn: true, fadeMs: 60 },
        calm: { pop: false, highlight: false, punchIn: false, fadeMs: 220 },
      },
    },
    cta: {
      copy: {
        subscribeSoft: "Subscribe for more real stories",
        subscribe: "Don't forget to subscribe",
        fullEpisode: "Listen to the full episode",
        related: "Full episode in the linked video below",
        handle: "@kintsugiheroes",
      },
      shortsTargets: {
        subscribeBtn: [430, 1715],
        channelProfile: [150, 1715],
        relatedLink: [300, 1830],
      },
      pillOpacity: 0.85,
      fontSize: 72,
    },
  },
};

const DEFAULT_AUDIOGRAM_FORMAT_LAYOUT = {
  padX: 6, padY: 6, logoH: 7.5, eyebrowSize: 2.2, captionSize: 4.0,
  waveformH: 22, barW: 2.2, barGap: 1.1, captionMaxWFrac: 0.72,
};

const DEFAULT_AUDIOGRAM_PROPS: AudiogramV2Props = {
  videoFileName: "input.mp4",
  title: "A story of repair",
  guestName: "Jane Doe",
  eyebrow: "Kintsugi Heroes",
  epLabel: null,
  caption: "The gold goes into the cracks",
  timedLines: null,
  amps: null,
  seedBars: Array.from({ length: 44 }, (_, i) => ({ h: 0.5, dur: 1.0, delay: i * 0.02 })),
  palette: { bg: "#424530", ink: "#FFF9ED", accent: "#ED9A1F", logoFileName: "Audio-Logo.png" },
  safety: "ok",
  width: 1920,
  height: 1080,
  fps: 30,
  durationInFrames: 180,
  brand: {
    fonts: {
      headingBold: { family: "KH Audio Heading", file: "assets/fonts/Archivo-Bold.ttf" },
      headingXBold: { family: "KH Audio Heading XBold", file: "assets/fonts/Archivo-ExtraBold.ttf" },
      body: { family: "KH Audio Body", file: "assets/fonts/OpenSans.ttf" },
      mono: { family: "KH Audio Mono", file: "assets/fonts/IBMPlexMono-Regular.ttf" },
    },
    bars: { count: 44, minScale: 0.34, entranceMs: 700, entranceStaggerMs: 12, springDamping: 16, springMass: 0.7, calmFadeMs: 320 },
    progress: { fillGlowMs: 260 },
    captionTransition: { fadeMs: 220, calmFadeMs: 420 },
    presets: {
      standard: { barEntranceSpring: true, progressGlow: true },
      calm: { barEntranceSpring: false, progressGlow: false },
    },
    layout: {
      wide: DEFAULT_AUDIOGRAM_FORMAT_LAYOUT,
      tall: { ...DEFAULT_AUDIOGRAM_FORMAT_LAYOUT, captionMaxWFrac: undefined, captionMaxWCqw: 84 },
      square: { ...DEFAULT_AUDIOGRAM_FORMAT_LAYOUT, captionMaxWFrac: undefined, captionMaxWCqw: 84 },
      barRadius: 0.7,
      centreGap: 5,
      progressH: 0.7,
      progressGap: 3.5,
    },
  },
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="KHKinetic"
        component={KHKinetic}
        durationInFrames={DEFAULT_PROPS.durationInFrames}
        fps={DEFAULT_PROPS.fps}
        width={DEFAULT_PROPS.width}
        height={DEFAULT_PROPS.height}
        defaultProps={DEFAULT_PROPS}
        calculateMetadata={async ({ props }) => {
          const introFrames = getIntroFrames(props.quoteCardIntro, props.banner, props.brand, props.fps);
          return {
            durationInFrames: props.durationInFrames + introFrames,
            fps: props.fps,
            width: props.width,
            height: props.height,
          };
        }}
      />
      <Composition
        id="KHAudiogramV2"
        component={KHAudiogramV2}
        durationInFrames={DEFAULT_AUDIOGRAM_PROPS.durationInFrames}
        fps={DEFAULT_AUDIOGRAM_PROPS.fps}
        width={DEFAULT_AUDIOGRAM_PROPS.width}
        height={DEFAULT_AUDIOGRAM_PROPS.height}
        defaultProps={DEFAULT_AUDIOGRAM_PROPS}
        calculateMetadata={async ({ props }) => ({
          durationInFrames: props.durationInFrames,
          fps: props.fps,
          width: props.width,
          height: props.height,
        })}
      />
    </>
  );
};

registerRoot(RemotionRoot);
