// KH Studio — POST /api/shorts/generate
//
// Creates a shorts_jobs row (RLS: the caller must have studio write access) and
// triggers the Modal worker. Keeps the Modal token server-side. Returns { job_id }.
//
// Drop this into the Studio repo at app/api/shorts/generate/route.ts and swap the
// Supabase client import for the project's existing server helper (the createClient
// used by other /api routes). Env needed (server only):
//   KH_SHORTS_WORKER_URL    -> the Modal endpoint printed by `modal deploy`
//   KH_SHORTS_WORKER_TOKEN  -> the same WORKER_TOKEN set in the Modal secret

import { NextRequest, NextResponse } from "next/server";
// TODO: replace with the project's server-side Supabase helper.
import { createClient } from "@/lib/supabase/server";

const SERIES = new Set([
  "golden-threads", "grit-diaries", "animals-and-us", "kintsugi-heroes",
  "connecting-seniors", "alpine-series", "australian-carers",
  "river-murray-recovery-stories",
]);

function youtubeId(url: string): string | null {
  const m = url.match(/(?:v=|youtu\.be\/|shorts\/|embed\/)([A-Za-z0-9_-]{11})/);
  return m ? m[1] : null;
}

export async function POST(req: NextRequest) {
  const supabase = createClient();

  // Must be a signed-in studio member (RLS enforces write access on insert).
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const body = await req.json().catch(() => ({}));
  const url: string = (body.url || "").trim();
  const series: string | null = body.series || null;
  const clip_count: number = Math.min(Math.max(parseInt(body.count ?? 5, 10) || 5, 1), 10);
  const audiogram: boolean = body.audiogram !== false;

  if (!youtubeId(url)) {
    return NextResponse.json({ error: "Enter a valid YouTube URL" }, { status: 400 });
  }
  if (series && !SERIES.has(series)) {
    return NextResponse.json({ error: "Unknown series" }, { status: 400 });
  }

  // 1) Create the job row (RLS check_write_section('studio') applies here).
  const { data: job, error } = await supabase
    .from("shorts_jobs")
    .insert({ url, series, clip_count, audiogram, status: "queued", progress: 0 })
    .select("id")
    .single();
  if (error || !job) {
    return NextResponse.json({ error: error?.message || "could not create job" }, { status: 403 });
  }

  // 2) Trigger the Modal worker (token stays server-side). Fire-and-forget; the
  //    worker drives the row to running/done/error and the UI watches via Realtime.
  try {
    const res = await fetch(process.env.KH_SHORTS_WORKER_URL!, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${process.env.KH_SHORTS_WORKER_TOKEN!}`,
      },
      body: JSON.stringify({
        job_id: job.id, url, series, count: clip_count, audiogram,
      }),
    });
    if (!res.ok) throw new Error(`worker ${res.status}`);
  } catch (e: any) {
    await supabase.from("shorts_jobs")
      .update({ status: "error", error: `trigger failed: ${e.message}` })
      .eq("id", job.id);
    return NextResponse.json({ error: "could not start the worker" }, { status: 502 });
  }

  return NextResponse.json({ job_id: job.id }, { status: 202 });
}
