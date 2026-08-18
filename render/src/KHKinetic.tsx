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

/** Wave 2 — KH Quote Card intro. How many frames the intro card occupies
 * before the footage cuts in — 0 when the feature is off, unavailable
 * globally (brand.animation.quoteCardIntro.enabled), or there's no hook line
 * to show (never render an intro on empty text). Exported so Root.tsx's
 * calculateMetadata can compute the SAME total composition length this
 * component actually lays out — one source of truth, no drift between the
 * two, the same discipline as caption.py's own duration/fps helpers. */
export const getIntroFrames = (
  quoteCardIntro: boolean | undefined,
  banner: string | null | undefined,
  brand: Brand,
  fps: number,
): number => {
  if (!quoteCardIntro || !banner || !banner.trim()) return 0;
  if (!brand.animation.quoteCardIntro.enabled) return 0;
  return msToFrames(brand.animation.quoteCardIntro.durationSec * 1000, fps);
};

/** Wave 2 — KH End Screen. How many frames the tail-window CTA overlay
 * occupies — 0 when the feature is off (either the per-clip `endScreenCta`
 * flag or the global `brand.animation.endScreen.enabled` kill-switch),
 * clamped to the clip's own `durationInFrames` so a genuinely short clip
 * never asks for a window bigger than the clip itself. Exported for the same
 * reason `getIntroFrames` above is: one place this math lives, so nothing
 * else can drift from what the component actually lays out. UNLIKE
 * `getIntroFrames`, this is never added to the total composition length —
 * the overlay occupies the TAIL of the clip's own existing duration (an
 * overlay, not an append), so Root.tsx's `calculateMetadata` needs no change
 * for this template. Note `durationInFrames` here is the VIDEO's own
 * duration (matching KhKineticProps' own field), the same length
 * `getIntroFrames`'s result gets ADDED to for the composition total — the
 * two features can coexist (an intro at the head, an overlay on the tail)
 * with neither needing to know about the other. */
export const getEndScreenFrames = (
  endScreenCta: boolean | undefined,
  brand: Brand,
  fps: number,
  durationInFrames: number,
): number => {
  if (endScreenCta === false) return 0;
  if (!brand.animation.endScreen.enabled) return 0;
  const raw = msToFrames(brand.animation.endScreen.windowSec * 1000, fps);
  return Math.max(1, Math.min(raw, durationInFrames));
};

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

/** Wave 2 — KH Quote Card intro: the clip's hook line as a full-bleed
 * typographic card for the first `quoteCardIntro.durationSec`, BEFORE the
 * footage cuts in (as opposed to the Banner above, which overlays the same
 * text ON the footage). Local frame 0 = the very start of the composition —
 * this component is never wrapped in an offsetting Sequence, so no fromMs
 * translation is needed here (unlike KHPage/KHToken, which live inside a
 * per-page Sequence and must subtract the page's own start).
 * Standard preset: fades in AND drifts/scales up (spring). CALM preset
 * (KH-TIC-001): fade only, no motion — the same fades-only rule the caption
 * pop-in and the banner already follow. */
const QuoteCardIntro: React.FC<{ text: string; brand: Brand; preset: PresetConfig; introFrames: number }> = ({
  text,
  brand,
  preset,
  introFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const fadeFrames = Math.max(1, msToFrames(preset.fadeMs, fps));
  const outStart = Math.max(0, introFrames - fadeFrames);
  const opacity =
    frame < fadeFrames
      ? frame / fadeFrames
      : frame < outStart
        ? 1
        : Math.max(0, (introFrames - frame) / fadeFrames);

  let translateY = 0;
  let scale = 1;
  if (preset.pop) {
    const entrance = spring({
      frame,
      fps,
      durationInFrames: fadeFrames,
      config: { damping: 16, mass: 0.7 },
    });
    translateY = brand.animation.quoteCardIntro.driftPx * (1 - entrance);
    scale = 0.96 + 0.04 * entrance;
  }

  return (
    <AbsoluteFill
      style={{
        backgroundColor: brand.colours.darkOlive,
        justifyContent: "center",
        alignItems: "center",
        padding: "0 90px",
        opacity,
      }}
    >
      <div style={{ transform: `translateY(${translateY}px) scale(${scale})`, textAlign: "center" }}>
        <div
          style={{
            color: brand.colours.creamWhite,
            fontFamily: brand.fonts.headingFamily,
            fontWeight: 600,
            fontSize: 92,
            lineHeight: 1.15,
          }}
        >
          {text.trim()}
        </div>
        <div
          style={{
            marginTop: 32,
            width: 120,
            height: 6,
            borderRadius: 3,
            background: brand.colours.gold,
            marginLeft: "auto",
            marginRight: "auto",
          }}
        />
      </div>
    </AbsoluteFill>
  );
};

/** KH-MGX-001 1.4 parity: the same gentle punch-in classic (libass) applies via
 * its zoompan filter: scale runs linearly from punchIn.startScale (1.00) to
 * punchIn.endScale (1.04) across the clip's own duration. No spring, no ease:
 * a linear ramp, matching classic's constant-step zoompan exactly. Reads every
 * value from brand.json (animation.punchIn, exported from src/brand.py), and
 * is DISABLED whenever the CALM preset applies (safety != "ok" ->
 * preset.punchIn false) or the global punchIn.enabled kill-switch is off,
 * the same two gates classic's caption.finish() checks. */
const PunchInVideo: React.FC<{
  videoFileName: string;
  brand: Brand;
  preset: PresetConfig;
  durationInFrames: number;
}> = ({ videoFileName, brand, preset, durationInFrames }) => {
  const frame = useCurrentFrame(); // local to mainContent, 0 at the footage's own start
  const p = brand.animation.punchIn;
  const enabled = p.enabled && preset.punchIn && p.endScale > p.startScale;
  const progress = durationInFrames > 1 ? Math.min(1, frame / durationInFrames) : 0;
  const scale = enabled ? p.startScale + (p.endScale - p.startScale) * progress : 1;
  return (
    <AbsoluteFill
      style={enabled ? { transform: `scale(${scale})`, transformOrigin: "center center" } : undefined}
    >
      <OffthreadVideo src={staticFile(videoFileName)} />
    </AbsoluteFill>
  );
};

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

/** Wave 2 — KH End Screen: a small gold down-arrow, drawn (never a text
 * glyph — the two self-hosted KH TTFs are heading/caption weights only and
 * carry no guarantee of an arrow codepoint, and this exactly mirrors why
 * src/cta.py's own `_arrow()` draws an ASS vector shape instead of a Unicode
 * character). Stem + downward triangle, same silhouette as the ASS path.
 * `target` is the SAME absolute canvas pixel pair `brand.cta.shortsTargets`
 * already carries for the classic CTA — centred `70px` above the target
 * (mirrors cta.py's `by - 70` / `px - 70`), so the tip points down at the
 * real native-UI button without covering it. */
const EndScreenArrow: React.FC<{ target: [number, number]; brand: Brand; opacity: number; size?: number }> = ({
  target,
  brand,
  opacity,
  size = 86,
}) => {
  const [x, y] = target;
  const stemH = size * 0.5;
  const headH = size * 0.5;
  const headW = size * 0.7;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y - 70,
        transform: "translate(-50%, -50%)",
        opacity,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
      }}
    >
      <div style={{ width: size * 0.28, height: stemH, background: brand.colours.gold }} />
      <div
        style={{
          width: 0,
          height: 0,
          borderLeft: `${headW / 2}px solid transparent`,
          borderRight: `${headW / 2}px solid transparent`,
          borderTop: `${headH}px solid ${brand.colours.gold}`,
        }}
      />
    </div>
  );
};

/** Wave 2 — KH End Screen: an animated CTA overlay over the FINAL SECONDS of
 * the clip (an overlay on the composition's own existing duration, NEVER
 * appended after — that is the retired src/endscreen.py pattern). Reuses
 * `brand.cta`'s copy and native-UI target pixels verbatim (exported from the
 * SAME brand.CTA dict src/cta.py's classic libass CTA already reads), so
 * kinetic and classic stay on the same message.
 *
 * Two variants, both built (the brief's own "correct parity target"):
 *   - "shorts": gold arrows point at YouTube's native Shorts UI buttons.
 *   - "universal": branded text only (the @handle), no arrows — for
 *     Reels/TikTok where the native buttons sit elsewhere.
 *
 * Deliberate departure from classic's own two TIME-ROTATED cards across its
 * 6s end window: at a 2-3s tail window (this template's own, shorter
 * `brand.animation.endScreen.windowSec`) there isn't enough time to rotate
 * through two cards and have either be legible, so both messages (subscribe,
 * then watch the full episode) STAGGER on together instead of taking turns —
 * everything stays on screen and readable for the whole window rather than
 * flashing past. Same copy, same gentle KH voice, adapted to a shorter,
 * purely-overlay window; see the PR body for the full reasoning.
 *
 * CALM preset (KH-TIC-001, locked decision #3): fade only, no spring
 * pop/drift — same rule as QuoteCardIntro and the caption pop-in.
 * (classic's own CTA, src/cta.py, has NO calm branch at all — it renders
 * full-energy regardless of `safety`; found while building this, flagged in
 * the PR body, not changed here — a separate, pre-existing component.)
 *
 * Real bug found on the first render, fixed here: showing all three lines
 * at once (vs. classic's one-line-at-a-time rotation) gives this a taller
 * footprint, and a hardcoded bottom offset collided with a 2-line caption
 * page in the (common) raised face-band. Fixed by anchoring off the SAME
 * `bandForCaptions()` the captions themselves use, plus a fixed clearance —
 * so the CTA block always sits a consistent, safe distance above wherever
 * the captions actually are, in either band, regardless of 1- or 2-line
 * wrap. See the PR body. */
const CAPTION_CLEARANCE_PX = 550;

const EndScreenCta: React.FC<{
  variant: "shorts" | "universal";
  brand: Brand;
  preset: PresetConfig;
  faceband: FaceBand;
}> = ({ variant, brand, preset, faceband }) => {
  const frame = useCurrentFrame(); // local to the wrapping Sequence — 0 at the window's own start
  const { fps } = useVideoConfig();
  const cta = brand.cta;
  const shorts = variant === "shorts";
  const fadeFrames = Math.max(1, msToFrames(preset.fadeMs, fps));
  const staggerFrames = msToFrames(brand.animation.endScreen.staggerMs, fps);
  const pillBottom = bandForCaptions(faceband, brand) + CAPTION_CLEARANCE_PX;

  const groupOpacity = (delayFrames: number): number => {
    const local = frame - delayFrames;
    return local < 0 ? 0 : Math.min(1, local / fadeFrames);
  };
  const groupOffset = (delayFrames: number): number => {
    if (!preset.pop) return 0; // CALM: fade only, no drift
    const local = frame - delayFrames;
    if (local < 0) return 18;
    const entrance = spring({ frame: local, fps, durationInFrames: fadeFrames, config: { damping: 16, mass: 0.7 } });
    return 18 * (1 - entrance);
  };

  const pillStyle: React.CSSProperties = {
    background: `rgba(45, 47, 34, ${cta.pillOpacity})`, // dark-olive pill, brand.cta.pillOpacity
    color: brand.colours.creamWhite,
    fontFamily: brand.fonts.headingFamily,
    fontWeight: 600,
    fontSize: cta.fontSize,
    padding: "16px 36px",
    borderRadius: 4,
    textAlign: "center",
  };

  const withAccent = (text: string, word: string): React.ReactNode => {
    const idx = text.toLowerCase().indexOf(word.toLowerCase());
    if (idx === -1) return text;
    return (
      <>
        {text.slice(0, idx)}
        <span style={{ color: brand.colours.gold }}>{text.slice(idx, idx + word.length)}</span>
        {text.slice(idx + word.length)}
      </>
    );
  };

  const subOpacity = groupOpacity(0);
  const subOffset = groupOffset(0);
  const episodeOpacity = groupOpacity(staggerFrames);
  const episodeOffset = groupOffset(staggerFrames);

  return (
    <>
      <div
        style={{
          position: "absolute",
          bottom: pillBottom, // caption band + fixed clearance — never collides, either band
          left: 60,
          right: 60,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 22,
        }}
      >
        <div style={{ opacity: subOpacity, transform: `translateY(${subOffset}px)` }}>
          <div style={pillStyle}>{withAccent(cta.copy.subscribe, "subscribe")}</div>
          {!shorts && (
            <div style={{ ...pillStyle, marginTop: 14, fontSize: cta.fontSize * 0.72 }}>{cta.copy.handle}</div>
          )}
        </div>
        <div
          style={{
            opacity: episodeOpacity,
            transform: `translateY(${episodeOffset}px)`,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div style={pillStyle}>{withAccent(cta.copy.fullEpisode, "full")}</div>
          <div style={pillStyle}>{cta.copy.related}</div>
        </div>
      </div>
      {shorts && (
        <>
          <EndScreenArrow target={cta.shortsTargets.subscribeBtn} brand={brand} opacity={subOpacity} />
          <EndScreenArrow target={cta.shortsTargets.channelProfile} brand={brand} opacity={episodeOpacity} />
          <EndScreenArrow
            target={cta.shortsTargets.relatedLink}
            brand={brand}
            opacity={episodeOpacity}
            size={64}
          />
        </>
      )}
    </>
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
  const {
    videoFileName,
    words,
    highlightWord,
    banner,
    safety,
    faceband,
    brand,
    durationInFrames,
    quoteCardIntro,
    variant,
    endScreenCta,
  } = props;
  const { fps } = useVideoConfig();
  const preset = presetFor(safety, brand);
  const introFrames = getIntroFrames(quoteCardIntro, banner, brand, fps);
  // Computed against the VIDEO's own durationInFrames (not the composition
  // total) — mainContent below is what actually plays for durationInFrames,
  // whether or not it's shifted later by an active intro, so the end-screen
  // Sequence nested INSIDE mainContent (not out here) lands on the tail of
  // the footage correctly in both cases with no extra offset math.
  const endScreenFrames = getEndScreenFrames(endScreenCta, brand, fps, durationInFrames);

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

  // The existing footage + captions + on-video banner, unchanged from before
  // the Quote Card intro existed — reused byte-for-byte whether or not the
  // intro is on, just optionally shifted later by `introFrames`.
  const mainContent = (
    <>
      <PunchInVideo
        videoFileName={videoFileName}
        brand={brand}
        preset={preset}
        durationInFrames={durationInFrames}
      />
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
      {/* Wave 2 — KH End Screen: an OVERLAY on the clip's own final seconds,
          not an append — a Sequence anchored to the tail of durationInFrames
          (the VIDEO's own duration, matching every other timing in
          mainContent), never extending it (unlike QuoteCardIntro's intro
          below, which genuinely lengthens the composition). Nested INSIDE
          mainContent so it correctly follows whatever offset an active
          intro applies to the whole block, with no extra math here. */}
      {endScreenFrames > 0 && (
        <Sequence from={durationInFrames - endScreenFrames} durationInFrames={endScreenFrames} layout="none">
          <EndScreenCta variant={variant || "shorts"} brand={brand} preset={preset} faceband={faceband} />
        </Sequence>
      )}
    </>
  );

  return (
    <AbsoluteFill style={{ backgroundColor: brand.colours.darkOlive }}>
      <FontFaces brand={brand} />
      {introFrames > 0 ? (
        <>
          <Sequence from={0} durationInFrames={introFrames} layout="none">
            <QuoteCardIntro text={banner as string} brand={brand} preset={preset} introFrames={introFrames} />
          </Sequence>
          {/* A nested Sequence resets local frame 0 for everything inside it,
              so mainContent's own internal timings (all computed relative to
              the video's own t=0) need no change at all when shifted here. */}
          <Sequence from={introFrames} durationInFrames={durationInFrames} layout="none">
            {mainContent}
          </Sequence>
        </>
      ) : (
        mainContent
      )}
    </AbsoluteFill>
  );
};
