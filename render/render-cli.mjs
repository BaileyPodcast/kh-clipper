#!/usr/bin/env node
/**
 * KH Clipper — Wave 2 (KH-MGX-001) render bridge.
 *
 * Node entry point src/kinetic.py (and, since the KH Audiogram v2 follow-up,
 * src/audiogram_v2.py) shells out to. Renders a Remotion composition to an
 * MP4 via the programmatic API (bundle -> selectComposition -> renderMedia).
 *
 * Two dispatch modes via `--composition` (default "kinetic", so every
 * existing kinetic.py call — which never passes this flag — is COMPLETELY
 * UNCHANGED, byte-for-byte, from the first Wave 2 PR):
 *
 *   --composition kinetic (default)
 *     node render-cli.mjs \
 *       --video <clip.mp4> --words <words.json> --brand <brand.json> \
 *       --out <out.mp4> --duration <seconds> --fps <fps> \
 *       [--highlight <word>] [--banner <text>] [--safety ok|review] \
 *       [--faceband <faceband.json>] [--width 1080] [--height 1920] \
 *       [--quote-card-intro]   (Wave 2: hook line as a full-bleed card
 *                               before the footage cuts in; needs
 *                               --banner set)
 *       [--variant shorts|universal] [--no-end-screen-cta]
 *                               (Wave 2: KH End Screen — an animated CTA
 *                               overlay on the clip's own final seconds, on
 *                               by default; --variant picks arrows-at-
 *                               native-UI ["shorts", the default] vs
 *                               branded-text-only ["universal"];
 *                               --no-end-screen-cta opts a single render
 *                               out of it entirely)
 *
 *   --composition audiogram-v2 (new, KH Audiogram v2)
 *     node render-cli.mjs --composition audiogram-v2 \
 *       --video <clip.mp4> --brand <brand.json> --props <audiogram-props.json> \
 *       --out <out.mp4>
 *     `--props` is a JSON file holding everything the KHAudiogramV2 template
 *     needs beyond brand.json + the staged video (title/guestName/eyebrow/
 *     epLabel/caption/timedLines/amps/seedBars/palette/safety/width/height/
 *     fps/durationInFrames/logoFile), precomputed by src/audiogram_v2.py —
 *     the shape is too data-heavy (a per-frame amplitude array, per-format
 *     layout) to sensibly flatten into individual CLI flags the way the
 *     kinetic path's simpler per-clip overrides are.
 *
 * Prints one JSON line to stdout on success: {"ok":true,"out":...,"render_ms":...}
 * Exits non-zero with an error on stderr on failure.
 */
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.dirname(HERE);

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) {
        out[key] = true;
      } else {
        out[key] = next;
        i++;
      }
    }
  }
  return out;
}

/** Bundles src/Root.tsx once and renders `compositionId` with `inputProps`,
 * from a staged `publicDir`, to `outPath`. Shared by both dispatch modes so
 * neither has to repeat the bundle/select/render boilerplate. */
async function renderComposition({ compositionId, inputProps, publicDir, outPath }) {
  const browserExecutable = process.env.REMOTION_BROWSER_EXECUTABLE || undefined;

  const serveUrl = await bundle({
    entryPoint: path.join(HERE, "src", "Root.tsx"),
    publicDir,
    onProgress: () => {},
  });

  const composition = await selectComposition({
    serveUrl,
    id: compositionId,
    inputProps,
    browserExecutable,
  });

  const outDir = path.dirname(outPath);
  if (outDir) fs.mkdirSync(outDir, { recursive: true });

  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: outPath,
    inputProps,
    browserExecutable,
    enforceAudioTrack: true, // the clip's own real audio, not silence
    pixelFormat: "yuv420p",
    audioCodec: "aac",
  });
}

// ---------------------------------------------------------------------
// Mode 1 (default): KHKinetic — UNCHANGED from the first Wave 2 PR.
// ---------------------------------------------------------------------
async function runKinetic(args) {
  for (const req of ["video", "words", "brand", "out", "duration", "fps"]) {
    if (!args[req]) {
      throw new Error(`missing required --${req}`);
    }
  }

  const words = JSON.parse(fs.readFileSync(args.words, "utf8"));
  const brand = JSON.parse(fs.readFileSync(args.brand, "utf8"));
  const faceband = args.faceband && fs.existsSync(args.faceband)
    ? JSON.parse(fs.readFileSync(args.faceband, "utf8"))
    : null;

  const width = parseInt(args.width || "1080", 10);
  const height = parseInt(args.height || "1920", 10);
  const fps = parseFloat(args.fps);
  const durationSec = parseFloat(args.duration);
  const durationInFrames = Math.max(1, Math.round(durationSec * fps));

  // Remotion serves local assets from a "public dir" via staticFile(); the
  // source clip + the two KH font files live outside the project (a temp
  // clip path, the repo's assets/fonts/), so stage them into one for this
  // render. Cheap (a copy, not a re-encode) and cleaned up after.
  const stageDir = fs.mkdtempSync(path.join(os.tmpdir(), "kh-kinetic-"));
  const videoFileName = "input" + path.extname(args.video);
  fs.copyFileSync(args.video, path.join(stageDir, videoFileName));
  const repoRoot = path.dirname(HERE);
  for (const [rel, fname] of [
    [brand.fonts.headingFile, "KH-Heading.ttf"],
    [brand.fonts.captionFile, "KH-Caption.ttf"],
  ]) {
    const src = path.join(repoRoot, rel);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(stageDir, fname));
    }
  }

  const inputProps = {
    videoFileName,
    words,
    highlightWord: args.highlight || null,
    banner: args.banner || null,
    safety: args.safety || "ok",
    faceband,
    brand,
    width,
    height,
    fps,
    durationInFrames,
    // Wave 2 — KH Quote Card intro: opt-in per render (--quote-card-intro),
    // off by default. calculateMetadata (Root.tsx) extends the composition's
    // total length to cover it; no separate duration math needed here.
    quoteCardIntro: Boolean(args["quote-card-intro"]),
    // Wave 2 — KH End Screen: on by default (parity with classic's own
    // always-on CTA, src/cta.py) — --no-end-screen-cta is the one opt-out.
    variant: (args.variant === "universal" ? "universal" : "shorts"),
    endScreenCta: !args["no-end-screen-cta"],
  };

  const t0 = Date.now();
  await renderComposition({
    compositionId: "KHKinetic",
    inputProps,
    publicDir: stageDir,
    outPath: args.out,
  });
  fs.rmSync(stageDir, { recursive: true, force: true });

  const renderMs = Date.now() - t0;
  // v1 render-cost note (Wave 2 acceptance criterion — full ai_usage_costs-
  // style tracking is a kh-studio-side follow-up): wall-clock render time.
  console.log(JSON.stringify({ ok: true, out: args.out, render_ms: renderMs }));
}

// ---------------------------------------------------------------------
// Mode 2: KHAudiogramV2.
// ---------------------------------------------------------------------
async function runAudiogramV2(args) {
  for (const req of ["video", "brand", "props", "out"]) {
    if (!args[req]) {
      throw new Error(`missing required --${req}`);
    }
  }

  const brand = JSON.parse(fs.readFileSync(args.brand, "utf8"));
  const audiogramBrand = brand.audiogramV2;
  if (!audiogramBrand) {
    throw new Error("brand.json has no audiogramV2 key — run `python -m src.export_brand` again");
  }
  const props = JSON.parse(fs.readFileSync(args.props, "utf8"));

  // Stage the source clip (audio track only — this template composites a
  // branded card, not the video passthrough KHKinetic does), the 4 design-
  // suite fonts and the resolved per-series logo PNG into one publicDir.
  const stageDir = fs.mkdtempSync(path.join(os.tmpdir(), "kh-audiogram-v2-"));
  const videoFileName = "input" + path.extname(args.video);
  fs.copyFileSync(args.video, path.join(stageDir, videoFileName));

  for (const [rel, fname] of [
    [audiogramBrand.fonts.headingBold.file, "Audio-HeadingBold.ttf"],
    [audiogramBrand.fonts.headingXBold.file, "Audio-HeadingXBold.ttf"],
    [audiogramBrand.fonts.body.file, "Audio-Body.ttf"],
    [audiogramBrand.fonts.mono.file, "Audio-Mono.ttf"],
  ]) {
    const src = path.join(REPO_ROOT, rel);
    if (fs.existsSync(src)) {
      fs.copyFileSync(src, path.join(stageDir, fname));
    }
  }

  if (props.logoFile) {
    const logoSrc = path.join(REPO_ROOT, props.logoFile);
    if (fs.existsSync(logoSrc)) {
      fs.copyFileSync(logoSrc, path.join(stageDir, "Audio-Logo.png"));
    }
  }

  const inputProps = {
    ...props,
    videoFileName,
    brand: audiogramBrand,
    palette: { ...props.palette, logoFileName: "Audio-Logo.png" },
  };
  delete inputProps.logoFile;

  const t0 = Date.now();
  await renderComposition({
    compositionId: "KHAudiogramV2",
    inputProps,
    publicDir: stageDir,
    outPath: args.out,
  });
  fs.rmSync(stageDir, { recursive: true, force: true });

  const renderMs = Date.now() - t0;
  console.log(JSON.stringify({ ok: true, out: args.out, render_ms: renderMs }));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const composition = args.composition || "kinetic";
  if (composition === "kinetic") {
    await runKinetic(args);
  } else if (composition === "audiogram-v2") {
    await runAudiogramV2(args);
  } else {
    throw new Error(`unknown --composition "${composition}" (expected "kinetic" or "audiogram-v2")`);
  }
}

main().catch((err) => {
  console.error(String((err && err.stack) || err));
  process.exit(1);
});
