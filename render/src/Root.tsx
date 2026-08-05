/**
 * KH Clipper — Wave 2 (KH-MGX-001) Remotion entry point.
 *
 * ONE composition for this PR: KHKinetic. calculateMetadata resolves the
 * real duration/fps/frame from the input props (render-cli.mjs already knows
 * these from ffprobe, same as Wave 1's caption.py `_duration()`/`_fps()`) so
 * Remotion never has to probe the video itself.
 */
import React from "react";
import { Composition, registerRoot } from "remotion";
import { KHKinetic } from "./KHKinetic";
import type { KhKineticProps } from "./brand-types";

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
      presets: {
        standard: { pop: true, highlight: true, punchIn: true, fadeMs: 60 },
        calm: { pop: false, highlight: false, punchIn: false, fadeMs: 220 },
      },
    },
  },
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="KHKinetic"
      component={KHKinetic}
      durationInFrames={DEFAULT_PROPS.durationInFrames}
      fps={DEFAULT_PROPS.fps}
      width={DEFAULT_PROPS.width}
      height={DEFAULT_PROPS.height}
      defaultProps={DEFAULT_PROPS}
      calculateMetadata={async ({ props }) => ({
        durationInFrames: props.durationInFrames,
        fps: props.fps,
        width: props.width,
        height: props.height,
      })}
    />
  );
};

registerRoot(RemotionRoot);
