/**
 * KH Clipper — Wave 2 (KH-MGX-001): the KH Kinetic template.
 *
 * The Remotion premium render path's first template: word-by-word sprung
 * captions, the clip's highlight_word oversized in gold, the olive pill
 * background carried over from the Wave 1 libass look. Adapted from
 * remotion-dev/template-tiktok's captions pattern (createTikTokStyleCaptions
 * paging word timings into caption groups), restyled 100% to KH brand — zero
 * hardcoded colours, fonts or timings; everything below reads `props.brand`
 * (exported from src/brand.py by `python -m src.export_brand`, the single
 * source of truth Python and React share).
 *
 * Trauma-informed (KH-TIC-001 / KH-MGX-001 locked decision #3): the CALM
 * preset (selected from `props.safety` in Root.tsx, same rule as Wave 1's
 * caption.py) renders fades only — no pop, no scale change, no highlight
 * oversizing. Energy never overrides dignity.
 */
import React from "react";
import {
  AbsoluteFill,
  OffthreadVideo,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { createTikTokStyleCaptions } from "@remotion/captions";
import type { Caption } from "@remotion/captions";
import type { KhKineticProps, PresetConfig, FaceBand, Brand } from "./brand-types";

// How close two words' timings must be (ms) to land in the same on-screen
// caption group before the maxWordsPerLine cap below splits it further. Kept
// tight (real speech pauses between separate thoughts run well over this) so
// two genuinely separate lines never fuse into one page just because the gap
// between them was short.
const COMBINE_WITHIN_MS = 350;

const cleanWord = (text: string): string =>
  (text || "").toLowerCase().replace(/[^a-z']/g, "");

type Page = { startMs: number; durationMs: number; tokens: { text: string; fromMs: number; toMs: number }[] };

/** createTikTokStyleCaptions groups by time proximity only, with no cap on
 * page size — a page can still grow into a wall of text. Re-chunk any page
 * over `maxWordsPerLine` tokens into Wave 1-style fixed-size lines (same
 * brand.caption.maxWordsPerLine cap the libass path already uses), so the
 * kinetic template reads with the same rhythm as the classic one. */
const rechunkPages = (pages: Page[], maxWordsPerLine: number): Page[] => {
  const out: Page[] = [];
  for (const page of pages) {
    const pageEndMs = page.startMs + page.durationMs;
    for (let i = 0; i < page.tokens.length; i += maxWordsPerLine) {
      const slice = page.tokens.slice(i, i + maxWordsPerLine);
      const isLastChunk = i + maxWordsPerLine >= page.tokens.length;
      const startMs = slice[0].fromMs;
      // Non-final chunks hand straight off to the next; the final chunk of a
      // page keeps any trailing pause the original page carried (so the last
      // line doesn't vanish the instant the last word ends).
      const endMs = isLastChunk ? Math.max(pageEndMs, slice[slice.length - 1].toMs) : slice[slice.length - 1].toMs;
      out.push({ startMs, durationMs: Math.max(1, endMs - startMs), tokens: slice });
    }
  }
  return out;
};

const presetFor = (safety: string, brand: Brand): PresetConfig =>
  safety && safety !== "ok" ? brand.animation.presets.calm : brand.animation.presets.standard;

const bandForCaptions = (faceband: FaceBand, brand: Brand): number => {
  const b = brand.animation.captionBands;
  if (!faceband || faceband.bottom == null) return b.defaultMarginVPx;
  return faceband.bottom >= b.lowFaceThreshold ? b.raisedMarginVPx : b.defaultMarginVPx;
};

const bandForBanner = (faceband: FaceBand, brand: Brand): number => {
  const b = brand.animation.bannerBands;
  if (!faceband || faceband.top == null) return b.defaultMarginVPx;
  return faceband.top <= b.highFaceThreshold ? b.midMarginVPx : b.defaultMarginVPx;
};

const msToFrames = (ms: number, fps: number): number => Math.round((ms / 1000) * fps);

/** @font-face declarations pointing at the two KH font files, copied into the
 * render's public dir alongside the video by render-cli.mjs. */
const FontFaces: React.FC<{ brand: Brand }> = ({ brand }) => (
  <style>{`
    @font-face {
      font-family: '${brand.fonts.headingFamily}';
      src: url('${staticFile("KH-Heading.ttf")}') format('truetype');
      font-weight: 600;
    }
    @font-face {
      font-family: '${brand.fonts.captionFamily}';
      src: url('${staticFile("KH-Caption.ttf")}') format('truetype');
      font-weight: 600;
    }
  `}</style>
);

const Banner: React.FC<{ text: string | null; brand: Brand; faceband: FaceBand; durationInFrames: number }> = ({
  text,
  brand,
  faceband,
  durationInFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!text) return null;
  const t0 = msToFrames(200, fps);
  const t1 = Math.min(msToFrames(5000, fps), Math.max(t0 + msToFrames(2500, fps), Math.round(durationInFrames * 0.55)));
  const fadeFrames = msToFrames(350, fps);
  const opacity =
    frame < t0
      ? 0
      : frame < t0 + fadeFrames
        ? (frame - t0) / fadeFrames
        : frame < t1 - fadeFrames
          ? 1
          : frame < t1
            ? Math.max(0, (t1 - frame) / fadeFrames)
            : 0;
  if (frame < t0 || frame > t1) return null;
  const marginTop = bandForBanner(faceband, brand);
  return (
    <div
      style={{
        position: "absolute",
        top: marginTop,
        left: 90,
        right: 90,
        display: "flex",
        justifyContent: "center",
        opacity,
      }}
    >
      <div
        style={{
          background: brand.colours.gold,
          color: brand.colours.darkOlive,
          fontFamily: brand.fonts.headingFamily,
          fontWeight: 600,
          fontSize: 80,
          padding: "18px 40px",
          borderRadius: 4,
          textAlign: "center",
        }}
      >
        {text.trim().replace(/\.$/, "")}
      </div>
    </div>
  );
};

const KHToken: React.FC<{
  text: string;
  isActive: boolean;
  isHighlight: boolean;
  activeSinceFrame: number;
  brand: Brand;
  preset: PresetConfig;
}> = ({ text, isActive, isHighlight, activeSinceFrame, brand, preset }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const anim = brand.animation;

  const colour = isActive || isHighlight ? brand.colours.gold : brand.colours.creamWhite;

  let scale = anim.restScale;
  if (isActive) {
    const target = isHighlight ? anim.highlightScale : anim.activeScale;
    if (preset.pop) {
      const pop = spring({
        frame: frame - activeSinceFrame,
        fps,
        durationInFrames: Math.max(1, msToFrames(anim.popMs, fps)),
        config: { damping: 14, mass: 0.6 },
      });
      scale = anim.popFromScale + (target - anim.popFromScale) * pop;
    } else {
      scale = target; // CALM: colour only, no scale
    }
  } else if (isHighlight && preset.highlight) {
    scale = anim.highlightScale; // emphasis word, static — on screen whenever visible
  }

  return (
    // A flex ITEM, not inline-block+margin: `transform: scale()` never
    // reflows layout (it's paint-only), so a word popped/highlighted up to
    // 115% on inline-block neighbours visually overlapped them (found via a
    // real render, not assumed). The flex row's `gap` (KHPage) reserves real
    // space between items regardless of any child's own transform, so a
    // scaled-up word grows into its own margin instead of its neighbour.
    <span
      style={{
        color: colour,
        display: "inline-block",
        transform: `scale(${scale / 100})`,
        transformOrigin: "center bottom",
      }}
    >
      {text}
    </span>
  );
};

const KHPage: React.FC<{
  page: { startMs: number; durationMs: number; tokens: { text: string; fromMs: number; toMs: number }[] };
  highlightWord: string | null;
  brand: Brand;
  preset: PresetConfig;
  faceband: FaceBand;
}> = ({ page, highlightWord, brand, preset, faceband }) => {
  const frame = useCurrentFrame(); // local to this Sequence — 0 at the page's own start
  const { fps } = useVideoConfig();
  const hl = preset.highlight && highlightWord ? cleanWord(highlightWord) : null;
  const marginBottom = bandForCaptions(faceband, brand);

  const fadeFrames = Math.max(1, msToFrames(preset.fadeMs, fps));
  const opacity = Math.min(1, frame / fadeFrames);

  return (
    <div
      style={{
        position: "absolute",
        bottom: marginBottom,
        left: 60,
        right: 60,
        display: "flex",
        justifyContent: "center",
        opacity,
      }}
    >
      <div
        style={{
          background: `rgba(45, 47, 34, ${brand.caption.boxOpacity})`, // dark-olive pill, brand.caption.boxOpacity from brand.py
          color: brand.colours.creamWhite,
          fontFamily: brand.fonts.captionFamily,
          fontWeight: 600,
          fontSize: brand.caption.fontSize,
          padding: "18px 32px",
          borderRadius: 4,
          // flex+gap, not inline text flow: `gap` reserves real layout space
          // between words regardless of a scaled-up neighbour's paint-only
          // transform (see KHToken) — plain inline-block+margin let a 115%
          // highlight word visually run into the next word (caught on a real
          // render, fixed here).
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: "0.15em 0.4em",
          WebkitTextStroke: `${brand.caption.outlinePx}px ${brand.colours.darkOlive}`,
        }}
      >
        {page.tokens.map((tok, i) => {
          const localFromMs = tok.fromMs - page.startMs;
          const localToMs = tok.toMs - page.startMs;
          const nowMs = (frame / fps) * 1000;
          const isActive = nowMs >= localFromMs && nowMs < localToMs;
          const isHighlight = !!hl && cleanWord(tok.text) === hl;
          return (
            <KHToken
              key={i}
              text={tok.text}
              isActive={isActive}
              isHighlight={isHighlight}
              activeSinceFrame={msToFrames(localFromMs, fps)}
              brand={brand}
              preset={preset}
            />
          );
        })}
      </div>
    </div>
  );
};

export const KHKinetic: React.FC<KhKineticProps> = (props) => {
  const { videoFileName, words, highlightWord, banner, safety, faceband, brand, durationInFrames } = props;
  const { fps } = useVideoConfig();
  const preset = presetFor(safety, brand);

  const captions: Caption[] = words.map((w) => ({
    text: w.text,
    startMs: Math.round(w.start * 1000),
    endMs: Math.round(w.end * 1000),
    timestampMs: null,
    confidence: null,
  }));

  const { pages: rawPages } = createTikTokStyleCaptions({
    captions,
    combineTokensWithinMilliseconds: COMBINE_WITHIN_MS,
  });
  const pages = rechunkPages(rawPages, brand.caption.maxWordsPerLine);

  return (
    <AbsoluteFill style={{ backgroundColor: brand.colours.darkOlive }}>
      <FontFaces brand={brand} />
      <OffthreadVideo src={staticFile(videoFileName)} />
      <Banner text={banner} brand={brand} faceband={faceband} durationInFrames={durationInFrames} />
      {pages.map((page, i) => {
        const from = msToFrames(page.startMs, fps);
        const dur = Math.max(1, msToFrames(page.durationMs, fps));
        return (
          <Sequence key={i} from={from} durationInFrames={dur} layout="none">
            <KHPage page={page} highlightWord={highlightWord} brand={brand} preset={preset} faceband={faceband} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
