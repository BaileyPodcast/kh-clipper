/**
 * KH Clipper — Wave 2 (KH-MGX-001): KH Audiogram v2.
 *
 * A Remotion render of the approved KH design-suite audiogram card (source
 * of truth: kh-studio's `lib/social-suite/suite/KHAudiogram.dc.html`, per
 * SHORTS-AUDIOGRAM-DESIGN-SPEC.md), replacing/augmenting the Pillow
 * frame-by-frame version (src/audiogram.py) for promo video (ties into
 * KH-VRL-001). Same look — palette per series, gold "kintsugi seam"
 * waveform, logo + eyebrow + caption + progress bar + title/guest/EP
 * footer — with real, genuinely animated elements Pillow's independent
 * per-frame compositing can't cleanly do: a spring-eased staggered
 * waveform-bar entrance, cross-fading timed caption transitions, and a
 * subtle completion glow on the progress fill.
 *
 * Layout geometry, fonts, spring/fade timings all read from
 * `props.brand.audiogramV2` (exported from src/brand.py's AUDIOGRAM_V2
 * block by `python -m src.export_brand`) — nothing here is hardcoded. The
 * per-series palette (bg/ink/accent/logo) is resolved server-side by
 * src/audiogram.py's existing resolve_brand()/palette_for() and passed as
 * `props.palette`, same mechanism the Pillow version already uses.
 *
 * Trauma-informed (KH-TIC-001 / KH-MGX-001 locked decision #3): the CALM
 * preset (any `props.safety != "ok"`) disables the spring-eased bar
 * entrance and the progress-fill glow — fades only, no pop, no bounce. The
 * reactive waveform bar HEIGHT itself (driven by the clip's real voice,
 * same seeded-envelope-plus-audio-scale maths as src/audiogram.py) and the
 * linear progress fill stay active under BOTH presets: they are a
 * continuous representation of the audio/playback position, the entire
 * point of an audiogram, not a decorative flourish — see the long comment
 * on AUDIOGRAM_V2 in src/brand.py for the full reasoning.
 */
import React from "react";
import {
  AbsoluteFill,
  Audio,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { AudiogramBrand, AudiogramFormatLayout, AudiogramV2Props } from "./audiogram-types";

type FormatKey = "wide" | "tall" | "square";

const msToFrames = (ms: number, fps: number): number => Math.max(1, Math.round((ms / 1000) * fps));

const formatKeyFor = (width: number, height: number): FormatKey =>
  width > height ? "wide" : height > width ? "tall" : "square";

const presetFor = (safety: string, brand: AudiogramBrand) =>
  safety && safety !== "ok" ? brand.presets.calm : brand.presets.standard;

/** @font-face declarations for the four KH design-suite fonts this template
 * uses (distinct from KH Kinetic's KH Heading / KH Caption pair) — copied
 * into the render's public dir by render-cli.mjs under fixed filenames. */
const FontFaces: React.FC<{ brand: AudiogramBrand }> = ({ brand }) => (
  <style>{`
    @font-face { font-family: '${brand.fonts.headingBold.family}'; src: url('${staticFile("Audio-HeadingBold.ttf")}') format('truetype'); font-weight: 700; }
    @font-face { font-family: '${brand.fonts.headingXBold.family}'; src: url('${staticFile("Audio-HeadingXBold.ttf")}') format('truetype'); font-weight: 800; }
    @font-face { font-family: '${brand.fonts.body.family}'; src: url('${staticFile("Audio-Body.ttf")}') format('truetype'); font-weight: 400; }
    @font-face { font-family: '${brand.fonts.mono.family}'; src: url('${staticFile("Audio-Mono.ttf")}') format('truetype'); font-weight: 400; }
  `}</style>
);

const WaveformRow: React.FC<{
  cqw: number;
  layout: AudiogramFormatLayout;
  barRadius: number;
  amps: number[][] | null;
  seedBars: { h: number; dur: number; delay: number }[];
  minScale: number;
  accent: string;
  preset: { barEntranceSpring: boolean };
  brand: AudiogramBrand;
}> = ({ cqw, layout, barRadius, amps, seedBars, minScale, accent, preset, brand }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;

  const waveformHpx = layout.waveformH * cqw;
  const barWpx = layout.barW * cqw;
  const barGapPx = layout.barGap * cqw;
  const radiusPx = barRadius * cqw;

  const entranceFrames = msToFrames(brand.bars.entranceMs, fps);
  const staggerFrames = Math.round((brand.bars.entranceStaggerMs / 1000) * fps);
  const calmFadeFrames = msToFrames(brand.bars.calmFadeMs, fps);

  // CALM: no per-bar spring pop — the whole row fades in as one group via
  // opacity only (fades only, no pop, no bounce). Standard: fully opaque
  // immediately, each bar springs in on its own stagger instead.
  const groupOpacity = preset.barEntranceSpring
    ? 1
    : interpolate(frame, [0, calmFadeFrames], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });

  return (
    <div
      style={{
        height: waveformHpx,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: barGapPx,
        opacity: groupOpacity,
      }}
    >
      {seedBars.map((bar, i) => {
        // scale (0..1, floored at minScale) is the REACTIVE component — the
        // real audio envelope when available, else a seeded decorative
        // oscillation identical to src/audiogram.py's fallback maths.
        const scale = amps
          ? minScale + (1 - minScale) * (amps[Math.min(frame, amps.length - 1)]?.[i] ?? 0)
          : (() => {
              const phase = ((t + bar.delay) / bar.dur) * 2 * Math.PI;
              const osc = 0.5 + 0.5 * Math.sin(phase);
              return minScale + (1 - minScale) * osc;
            })();

        // entrance (0..~1, standard only) — a genuine spring, staggered
        // left-to-right, the thing Pillow's per-frame compositing can't do.
        const entrance = preset.barEntranceSpring
          ? spring({
              frame: frame - i * staggerFrames,
              fps,
              durationInFrames: entranceFrames,
              config: { damping: brand.bars.springDamping, mass: brand.bars.springMass },
            })
          : 1;

        const heightPx = Math.max(1, bar.h * waveformHpx * scale * entrance);
        return (
          <div
            key={i}
            style={{
              width: barWpx,
              height: heightPx,
              borderRadius: radiusPx,
              backgroundColor: accent,
              flexShrink: 0,
            }}
          />
        );
      })}
    </div>
  );
};

const Caption: React.FC<{
  cqw: number;
  layout: AudiogramFormatLayout;
  width: number;
  caption: string | null;
  timedLines: { start: number; end: number; text: string }[] | null;
  ink: string;
  headingFont: string;
  fadeMs: number;
}> = ({ cqw, layout, width, caption, timedLines, ink, headingFont, fadeMs }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const fadeSec = fadeMs / 1000;

  const maxW = layout.captionMaxWFrac != null ? width * layout.captionMaxWFrac : (layout.captionMaxWCqw ?? 84) * cqw;
  const fontSize = layout.captionSize * cqw;

  let text: string | null = null;
  let opacity = 0;

  if (timedLines && timedLines.length) {
    const active = timedLines.find((l) => t >= l.start && t < l.end);
    if (active) {
      text = active.text;
      const distStart = t - active.start;
      const distEnd = active.end - t;
      const fadeIn = fadeSec > 0 ? Math.min(1, distStart / fadeSec) : 1;
      const fadeOut = fadeSec > 0 ? Math.min(1, distEnd / fadeSec) : 1;
      opacity = Math.max(0, Math.min(fadeIn, fadeOut));
    }
  } else if (caption) {
    text = caption;
    const fadeFrames = msToFrames(fadeMs, fps);
    opacity = interpolate(frame, [0, fadeFrames], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  }

  if (!text) return null;

  return (
    <div
      style={{
        maxWidth: maxW,
        margin: "0 auto",
        textAlign: "center",
        color: ink,
        fontFamily: headingFont,
        fontWeight: 700,
        fontSize,
        lineHeight: 1.28,
        opacity,
        display: "-webkit-box",
        WebkitLineClamp: 3,
        WebkitBoxOrient: "vertical",
        overflow: "hidden",
      }}
    >
      {text}
    </div>
  );
};

const ProgressBar: React.FC<{
  cqw: number;
  progressH: number;
  accent: string;
  fps: number;
  frame: number;
  durationInFrames: number;
  glowMs: number;
  glowEnabled: boolean;
}> = ({ cqw, progressH, accent, fps, frame, durationInFrames, glowMs, glowEnabled }) => {
  const progressHpx = progressH * cqw;
  const frac = Math.min(1, (frame + 1) / durationInFrames);

  const glowFrames = msToFrames(glowMs, fps);
  const glowT = glowEnabled
    ? interpolate(frame, [Math.max(0, durationInFrames - glowFrames), durationInFrames - 1], [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  return (
    <div
      style={{
        position: "relative",
        height: progressHpx,
        borderRadius: 999,
        backgroundColor: "rgba(45, 47, 34, 0.14)",
        overflow: "visible",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: `${Math.max(progressHpx, frac * 100)}%`,
          borderRadius: 999,
          backgroundColor: accent,
          boxShadow: glowT > 0 ? `0 0 ${glowT * cqw * 1.6}px ${glowT * cqw * 0.4}px ${accent}` : "none",
        }}
      />
    </div>
  );
};

export const KHAudiogramV2: React.FC<AudiogramV2Props> = (props) => {
  const {
    videoFileName,
    title,
    guestName,
    eyebrow,
    epLabel,
    caption,
    timedLines,
    amps,
    seedBars,
    palette,
    safety,
    brand,
    width,
    height,
  } = props;
  const { fps, durationInFrames } = useVideoConfig();
  const frame = useCurrentFrame();

  const preset = presetFor(safety, brand);
  const cqw = Math.min(width, height) / 100;
  const px = (v: number) => v * cqw;
  const formatKey = formatKeyFor(width, height);
  const layout = brand.layout[formatKey];

  const padX = px(layout.padX);
  const padY = px(layout.padY);
  const eyebrowFontSize = px(layout.eyebrowSize);
  const logoHpx = px(layout.logoH);
  const borderInset = px(3.4);
  const borderWidth = Math.max(2, cqw * 0.18);
  const centreGap = px(brand.layout.centreGap);
  const progressGap = px(brand.layout.progressGap);

  const captionFadeMs = safety && safety !== "ok" ? brand.captionTransition.calmFadeMs : brand.captionTransition.fadeMs;

  return (
    <AbsoluteFill style={{ backgroundColor: palette.bg }}>
      <FontFaces brand={brand} />
      <Audio src={staticFile(videoFileName)} />

      {/* Subtle top radial highlight, matching audiogram.py's compositing exactly. */}
      <AbsoluteFill
        style={{
          background: "radial-gradient(118% 82% at 50% 8%, rgba(255,255,255,0.10), transparent 56%)",
        }}
      />

      {/* Accent inset border ring. */}
      <div
        style={{
          position: "absolute",
          left: borderInset,
          top: borderInset,
          right: borderInset,
          bottom: borderInset,
          border: `${borderWidth}px solid ${palette.accent}`,
          opacity: 0.26,
          borderRadius: 4,
        }}
      />

      <AbsoluteFill style={{ padding: `${padY}px ${padX}px`, display: "flex", flexDirection: "column" }}>
        {/* Top row: logo left, eyebrow right. */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <img src={staticFile("Audio-Logo.png")} style={{ height: logoHpx, opacity: 0.9 }} alt="" />
          <div
            style={{
              color: palette.accent,
              fontFamily: brand.fonts.headingBold.family,
              fontWeight: 700,
              fontSize: eyebrowFontSize,
              letterSpacing: `${eyebrowFontSize * 0.2}px`,
              textTransform: "uppercase",
            }}
          >
            {(eyebrow || "").toUpperCase()}
          </div>
        </div>

        {/* Centre: waveform + caption, vertically centred in the free space. */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: centreGap }}>
          <WaveformRow
            cqw={cqw}
            layout={layout}
            barRadius={brand.layout.barRadius}
            amps={amps}
            seedBars={seedBars}
            minScale={brand.bars.minScale}
            accent={palette.accent}
            preset={preset}
            brand={brand}
          />
          <Caption
            cqw={cqw}
            layout={layout}
            width={width}
            caption={caption}
            timedLines={timedLines}
            ink={palette.ink}
            headingFont={brand.fonts.headingBold.family}
            fadeMs={captionFadeMs}
          />
        </div>

        {/* Bottom: progress bar + footer (title / guest / EP). */}
        <div>
          <ProgressBar
            cqw={cqw}
            progressH={brand.layout.progressH}
            accent={palette.accent}
            fps={fps}
            frame={frame}
            durationInFrames={durationInFrames}
            glowMs={brand.progress.fillGlowMs}
            glowEnabled={preset.progressGlow}
          />
          <div style={{ height: progressGap }} />
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
            <div>
              {title ? (
                <div
                  style={{
                    color: palette.ink,
                    fontFamily: brand.fonts.headingXBold.family,
                    fontWeight: 800,
                    fontSize: px(2.8),
                  }}
                >
                  {title}
                </div>
              ) : null}
              {guestName ? (
                <div
                  style={{
                    color: palette.ink,
                    opacity: 0.6,
                    fontFamily: brand.fonts.body.family,
                    fontWeight: 400,
                    fontSize: px(2.0),
                    marginTop: px(0.5),
                  }}
                >
                  with {guestName}
                </div>
              ) : null}
            </div>
            {epLabel ? (
              <div
                style={{
                  color: palette.ink,
                  opacity: 0.5,
                  fontFamily: brand.fonts.mono.family,
                  fontWeight: 400,
                  fontSize: px(1.8),
                }}
              >
                {epLabel}
              </div>
            ) : null}
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
