"use client";
// KH Studio — /studio/shorts
//
// Paste a YouTube URL, pick the series, click Generate Shorts. Watches the
// shorts_jobs row via Supabase Realtime for live progress, then shows a results
// gallery: per-clip video preview (signed URL), safety/framing flags, copy-paste
// metadata, audiograms, an approve toggle, and download.
//
// Drop into the Studio repo at app/studio/shorts/page.tsx. Swap the Supabase client
// import for the project's browser helper. The page assumes the `studio` section is
// gated by the same layout/access guard used by other /studio pages.

import { useEffect, useState } from "react";
// TODO: replace with the project's browser Supabase helper.
import { createClient } from "@/lib/supabase/client";

const SERIES = [
  ["golden-threads", "Golden Threads"],
  ["grit-diaries", "Grit Diaries"],
  ["animals-and-us", "Animals & Us"],
  ["kintsugi-heroes", "Kintsugi Heroes"],
  ["connecting-seniors", "Connecting Seniors"],
  ["alpine-series", "Alpine Series"],
  ["australian-carers", "Australian Carers"],
  ["river-murray-recovery-stories", "River Murray Recovery Stories"],
];

type Clip = {
  clip_id: string; hook_line: string; why: string;
  safety: string; safety_note?: string; framing: string;
  metadata: { title: string; description: string; hashtags: string[];
              pinned_comment: string; banner_hook: string };
  files: Record<string, string>; // kind -> "shorts/<job>/<file>"
};
type Job = {
  id: string; status: string; stage?: string; progress: number;
  message?: string; error?: string;
  outputs?: { clips: Clip[]; review?: string };
};

export default function ShortsPage() {
  const supabase = createClient();
  const [url, setUrl] = useState("");
  const [series, setSeries] = useState("golden-threads");
  const [count, setCount] = useState(5);
  const [audiogram, setAudiogram] = useState(true);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // Live progress: subscribe to the job row.
  useEffect(() => {
    if (!job?.id) return;
    const ch = supabase
      .channel(`shorts_jobs:${job.id}`)
      .on("postgres_changes",
        { event: "UPDATE", schema: "public", table: "shorts_jobs", filter: `id=eq.${job.id}` },
        (p) => setJob((j) => ({ ...(j as Job), ...(p.new as Job) })))
      .subscribe();
    return () => { supabase.removeChannel(ch); };
  }, [job?.id]);

  async function generate() {
    setErr(""); setBusy(true); setJob(null);
    try {
      const res = await fetch("/api/shorts/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, series, count, audiogram }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "failed");
      setJob({ id: data.job_id, status: "queued", progress: 0 });
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="mx-auto max-w-3xl p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Shorts Engine</h1>
      <p className="text-sm text-muted-foreground">
        Paste an episode URL, pick the series, and generate 5 branded Shorts + audiograms.
        Nothing publishes automatically — review and approve below.
      </p>

      <div className="space-y-3 rounded-xl border p-4">
        <input className="w-full rounded border px-3 py-2" placeholder="YouTube URL"
               value={url} onChange={(e) => setUrl(e.target.value)} />
        <div className="flex flex-wrap gap-3">
          <select className="rounded border px-3 py-2" value={series}
                  onChange={(e) => setSeries(e.target.value)}>
            {SERIES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <select className="rounded border px-3 py-2" value={count}
                  onChange={(e) => setCount(parseInt(e.target.value, 10))}>
            {[3, 4, 5, 6].map((n) => <option key={n} value={n}>{n} clips</option>)}
          </select>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={audiogram}
                   onChange={(e) => setAudiogram(e.target.checked)} /> Audiograms
          </label>
          <button onClick={generate} disabled={busy || !url}
                  className="ml-auto rounded bg-black px-4 py-2 text-white disabled:opacity-50">
            {busy ? "Starting…" : "Generate Shorts"}
          </button>
        </div>
        {err && <p className="text-sm text-red-600">{err}</p>}
      </div>

      {job && job.status !== "done" && job.status !== "error" && (
        <div className="rounded-xl border p-4">
          <div className="mb-2 flex justify-between text-sm">
            <span>{job.stage || job.status} — {job.message || ""}</span>
            <span>{job.progress}%</span>
          </div>
          <div className="h-2 w-full rounded bg-gray-200">
            <div className="h-2 rounded bg-black transition-all" style={{ width: `${job.progress}%` }} />
          </div>
        </div>
      )}
      {job?.status === "error" && (
        <div className="rounded-xl border border-red-300 bg-red-50 p-4 text-sm text-red-700">
          <p>Failed: {job.error}</p>
          <button onClick={generate} disabled={busy}
                  className="mt-3 rounded bg-red-600 px-4 py-2 text-white disabled:opacity-50">
            {busy ? "Retrying…" : "Retry"}
          </button>
        </div>
      )}

      {/* Stuck/queued escape hatch: lets the producer restart if it stalls. */}
      {job && (job.status === "queued" || job.status === "running") && (
        <button onClick={generate} disabled={busy}
                className="text-xs text-gray-500 underline">
          Taking too long? Start again
        </button>
      )}

      {job?.status === "done" && job.outputs && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <p className="text-sm text-green-700">{job.outputs.clips.length} clips ready.</p>
            <button onClick={generate} disabled={busy}
                    className="rounded border px-3 py-1.5 text-sm disabled:opacity-50">
              {busy ? "Starting…" : "Run again"}
            </button>
          </div>
          {job.outputs.clips.map((c, i) => <ClipCard key={c.clip_id} clip={c} n={i + 1} />)}
        </div>
      )}
    </div>
  );
}

function ClipCard({ clip, n }: { clip: Clip; n: number }) {
  const supabase = createClient();
  const [src, setSrc] = useState<string>("");
  const [approved, setApproved] = useState(false);

  // Signed URL for the shorts preview (private bucket; studio members may read).
  useEffect(() => {
    const path = clip.files.shorts || Object.values(clip.files)[0];
    if (!path) return;
    const [bucket, ...rest] = path.split("/");
    supabase.storage.from(bucket).createSignedUrl(rest.join("/"), 3600)
      .then(({ data }) => data?.signedUrl && setSrc(data.signedUrl));
  }, [clip]);

  const flagged = clip.safety !== "ok" || clip.framing !== "ok";
  return (
    <div className="rounded-xl border p-4">
      <div className="flex gap-4">
        {src
          ? <video src={src} controls className="h-72 rounded bg-black" />
          : <div className="h-72 w-40 rounded bg-gray-100" />}
        <div className="flex-1 space-y-2">
          <div className="flex items-center gap-2">
            <span className="font-medium">Clip {n}</span>
            <Flag ok={clip.safety === "ok"} label={clip.safety === "ok" ? "safe" : `review: ${clip.safety_note || clip.safety}`} />
            <Flag ok={clip.framing === "ok"} label={clip.framing === "ok" ? "framed" : "framing review"} />
          </div>
          <p className="text-sm italic">“{clip.hook_line}”</p>
          <p className="text-xs text-muted-foreground">{clip.why}</p>
          <Copy label="Title" text={clip.metadata?.title} />
          <Copy label="Description" text={clip.metadata?.description} />
          <Copy label="Hashtags" text={(clip.metadata?.hashtags || []).join(" ")} />
          <Copy label="Pinned comment" text={clip.metadata?.pinned_comment} />
          <label className={`mt-2 flex items-center gap-2 text-sm ${flagged ? "text-amber-700" : ""}`}>
            <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
            Approved {flagged && "(open the clip first — flagged for review)"}
          </label>
        </div>
      </div>
    </div>
  );
}

function Flag({ ok, label }: { ok: boolean; label: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs ${ok ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-800"}`}>{label}</span>;
}

function Copy({ label, text }: { label: string; text?: string }) {
  if (!text) return null;
  return (
    <div className="flex items-start gap-2 text-sm">
      <button onClick={() => navigator.clipboard.writeText(text)}
              className="shrink-0 rounded border px-2 py-0.5 text-xs">Copy {label}</button>
      <span className="text-muted-foreground line-clamp-2">{text}</span>
    </div>
  );
}
