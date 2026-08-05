#!/usr/bin/env node
/**
 * KH Clipper — Wave 2 (KH-MGX-001) render bridge.
 *
 * Node entry point src/kinetic.py shells out to. Takes a REFRAMED, UNBURNED
 * 9:16 clip + clip-relative word timings + brand.json (the input contract the
 * brief specifies) and renders the KH Kinetic composition to an MP4 via
 * Remotion's programmatic API (bundle -> selectComposition -> renderMedia).
 *
 * Usage:
 *   node render-cli.mjs \
 *     --video <clip.mp4> --words <words.json> --brand <brand.json> \
 *     --out <out.mp4> --duration <seconds> --fps <fps> \
 *     [--highlight <word>] [--banner <text>] [--safety ok|review] \
 *     [--faceband <faceband.json>] [--width 1080] [--height 1920] \
 *     [--quote-card-intro]   (Wave 2: hook line as a full-bleed card before
 *                             the footage cuts in; needs --banner set)
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

async function main() {
  const args = parseArgs(process.argv.slice(2));
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
  };

  const t0 = Date.now();
  const browserExecutable = process.env.REMOTION_BROWSER_EXECUTABLE || undefined;

  const serveUrl = await bundle({
    entryPoint: path.join(HERE, "src", "Root.tsx"),
    publicDir: stageDir,
    onProgress: () => {},
  });

  const composition = await selectComposition({
    serveUrl,
    id: "KHKinetic",
    inputProps,
    browserExecutable,
  });

  const outDir = path.dirname(args.out);
  if (outDir) fs.mkdirSync(outDir, { recursive: true });

  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: args.out,
    inputProps,
    browserExecutable,
    enforceAudioTrack: true, // the clip's own real audio, not silence
    pixelFormat: "yuv420p",
    audioCodec: "aac",
  });

  fs.rmSync(stageDir, { recursive: true, force: true });

  const renderMs = Date.now() - t0;
  // v1 render-cost note (Wave 2 acceptance criterion — full ai_usage_costs-
  // style tracking is a kh-studio-side follow-up): wall-clock render time.
  console.log(JSON.stringify({ ok: true, out: args.out, render_ms: renderMs }));
}

main().catch((err) => {
  console.error(String((err && err.stack) || err));
  process.exit(1);
});
