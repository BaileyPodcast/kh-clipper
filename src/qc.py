"""
KH episode QC: mechanical checks on a finished episode master (KH-QC-001).

A finished episode can be broken in ways nobody catches without sitting through
all 60 minutes: a black frame at a bad splice, one segment never levelled, a
duplicate take left in, audio that drifts away from picture. This module is the
FILE side of that check. The transcript side lives in src/qc_transcript.py.

House style, same as src/loudness.py: every ffmpeg/ffprobe call is split into a
PURE command builder plus a PURE parser, so the whole detection surface is unit
testable with no ffmpeg on the machine, and a thin runner that NEVER raises. A
QC pass that dies halfway is worse than one that reports "could not measure":
the worker turns a missing measurement into an honest `info` finding, and a
crash would instead leave the run stuck.

Nothing in here decides severity or writes to the database. It measures, and
hands back numbers.
"""
from __future__ import annotations

import json
import re
import subprocess

# ----------------------------------------------------------------------
# Detection thresholds. Each one is the point where a human would actually
# want to look, not the point where ffmpeg can technically see something.
# ----------------------------------------------------------------------
BLACK_MIN_DUR = 0.5        # seconds of black before it reads as a bad splice
BLACK_PIX_TH = 0.10        # per-pixel black threshold (ffmpeg default)
FREEZE_MIN_DUR = 2.0       # a 2s frozen picture is a stall, not a still moment
FREEZE_NOISE_DB = "-60dB"  # ffmpeg default freeze sensitivity
SILENCE_NOISE_DB = -45     # dBFS floor that counts as near-silence
SILENCE_MIN_DUR = 0.6      # shorter than this is ordinary speech rhythm

# Analysis passes decode the whole file, so give them room: a 90 minute master
# on a Modal CPU container decodes well inside this. Deliberately shorter than
# the worker function's own timeout, so one wedged pass returns a "could not
# measure" finding instead of taking the container down mid-run.
ANALYSIS_TIMEOUT = 1200
PROBE_TIMEOUT = 180

# Per check type confidence, straight from the KH-QC-001 contract table. It is
# carried through to the UI so a volunteer learns which flags to trust without
# listening to the episode, so it has to be honest rather than decorative.
# The five types the contract's table does not list (av_sync, file_identity,
# episode_length, export_settings, transcript_mismatch) are all direct
# measurements of the file or its container, so they sit with the other
# measured checks at 0.95.
CONFIDENCE = {
    "black_frame": 0.95,
    "freeze_frame": 0.95,
    "loudness": 0.95,
    "clipping": 0.95,
    "duration": 0.95,
    "resolution": 0.95,
    "av_sync": 0.95,
    "file_identity": 0.95,
    "episode_length": 0.95,
    "export_settings": 0.95,
    "transcript_mismatch": 0.95,
    "no_go_topic": 0.9,
    "silence_gap": 0.85,
    "captions_sync": 0.8,
    "speaker_count": 0.8,
    "duplicate_segment": 0.7,
    "mid_word_cut": 0.6,
    "segment_order": 0.5,
}

CHECK_TYPES = set(CONFIDENCE)


def make_finding(run_id, episode_id, check_type, severity, detail,
                 start=None, end=None):
    """One episode_qc_checks row. Severity for `no_go_topic` is settled here
    rather than trusted to each call site, and confidence always comes from the
    contract table, never from the caller."""
    # A no-go hit is ALWAYS an error: it is a consent breach, not a quality
    # note, and it is the one check kh-studio blocks on with no override. The
    # single exception is an `info` row, which is how the worker says it could
    # not look at all. Turning that into an error would report a breach the
    # worker never found.
    severity = str(severity)
    if check_type == "no_go_topic" and severity != "info":
        severity = "error"
    row = {
        "run_id": run_id,
        "check_type": check_type,
        "severity": severity,
        "confidence": CONFIDENCE.get(check_type, 0.5),
        "detail": str(detail)[:1000],
    }
    if episode_id:
        row["studio_episode_id"] = episode_id
    if start is not None:
        row["timestamp_start"] = round(float(start), 2)
    if end is not None:
        row["timestamp_end"] = round(float(end), 2)
    return row


def timecode(seconds):
    """Seconds as h:mm:ss (or m:ss under an hour) for a human-readable detail."""
    try:
        s = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "?"
    h, rem = divmod(max(s, 0), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ----------------------------------------------------------------------
# Thin runner. Analysis filters write their findings to stderr, so the caller
# always gets stderr back; a failure yields empty strings rather than an
# exception, and the worker reports the gap as an info finding.
# ----------------------------------------------------------------------
def run_capture(cmd, timeout=ANALYSIS_TIMEOUT):
    """Run a command and return (ok, stdout, stderr). Never raises.

    `ok` is what keeps QC honest: empty analysis output means "found nothing"
    when the pass ran and "we never looked" when it did not, and those two must
    never collapse into the same clean-looking result. The caller turns ok=False
    into an `info` finding naming the gap.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout or "", r.stderr or ""
    except Exception as e:
        print(f"      ~ qc probe failed ({str(e)[:160]})")
        return False, "", ""


def _float(value):
    """float(value) or None. Handles ffmpeg's '-inf' and stray formatting."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


# ----------------------------------------------------------------------
# Black frames: a run of black at a splice point.
# ----------------------------------------------------------------------
def build_blackdetect_command(path):
    """ffmpeg command that reports every black run. Video only, no output file."""
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path,
        "-vf", f"blackdetect=d={BLACK_MIN_DUR}:pix_th={BLACK_PIX_TH}",
        "-an", "-f", "null", "-",
    ]


def parse_blackdetect(stderr):
    """[(start, end)] from blackdetect's stderr lines. Pure."""
    out = []
    for m in re.finditer(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)",
                         stderr or ""):
        start, end = _float(m.group(1)), _float(m.group(2))
        if start is not None and end is not None:
            out.append((start, end))
    return out


# ----------------------------------------------------------------------
# Freeze frames: picture stalled while the audio keeps running.
# ----------------------------------------------------------------------
def build_freezedetect_command(path):
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path,
        "-vf", f"freezedetect=n={FREEZE_NOISE_DB}:d={FREEZE_MIN_DUR}",
        "-an", "-f", "null", "-",
    ]


def parse_freezedetect(stderr):
    """[(start, end)] from freezedetect. freeze_start and freeze_end arrive on
    separate lines, so they are paired in order. A freeze that runs to the end
    of the file never gets its end line, and is returned as (start, None) so the
    caller can still flag it rather than dropping it. Pure."""
    events = re.findall(r"lavfi\.freezedetect\.freeze_(start|end):\s*([0-9.-]+)",
                        stderr or "")
    out, open_start = [], None
    for kind, value in events:
        v = _float(value)
        if v is None:
            continue
        if kind == "start":
            if open_start is not None:
                out.append((open_start, None))
            open_start = v
        elif open_start is not None:
            out.append((open_start, v))
            open_start = None
    if open_start is not None:
        out.append((open_start, None))
    return out


# ----------------------------------------------------------------------
# Silence: dead air, a dropped word at a cut, one unlevelled segment.
# ----------------------------------------------------------------------
def build_silencedetect_command(path, noise_db=SILENCE_NOISE_DB,
                                min_dur=SILENCE_MIN_DUR):
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-vn", "-f", "null", "-",
    ]


def parse_silencedetect(stderr):
    """[(start, end)] from silencedetect. Same paired-line shape as freeze, and
    the same open-ended case: silence running to the end of the file yields
    (start, None). Pure."""
    events = re.findall(r"silence_(start|end):\s*([0-9.-]+)", stderr or "")
    out, open_start = [], None
    for kind, value in events:
        v = _float(value)
        if v is None:
            continue
        if kind == "start":
            if open_start is not None:
                out.append((open_start, None))
            open_start = v
        elif open_start is not None:
            out.append((open_start, v))
            open_start = None
    if open_start is not None:
        out.append((open_start, None))
    return out


# ----------------------------------------------------------------------
# Loudness: is the episode inside the platform target, and does it peak safely.
# ----------------------------------------------------------------------
def build_ebur128_command(path):
    """peak=true so the summary carries a TRUE peak, not just a sample peak.
    True peak is what the platforms measure against, and inter-sample overs are
    invisible to a sample-peak reading."""
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path,
        "-af", "ebur128=peak=true", "-vn", "-f", "null", "-",
    ]


def parse_ebur128(stderr):
    """{integrated_lufs, true_peak_dbtp, lra} from the ebur128 Summary block.
    The summary prints 'Peak:' under BOTH a 'Sample peak:' and a 'True peak:'
    heading, so the section header is tracked and only the true peak is taken.
    Missing values come back as None rather than a guess. Pure."""
    integrated = true_peak = lra = None
    section = None
    for raw in (stderr or "").splitlines():
        line = re.sub(r"^\[[^\]]*\]\s*", "", raw).strip()
        if line.lower().startswith("true peak"):
            section = "true"
            continue
        if line.lower().startswith("sample peak"):
            section = "sample"
            continue
        if line.lower().startswith(("integrated loudness", "loudness range")):
            section = None
        m = re.match(r"I:\s*(-?[0-9.]+|-inf)\s*LUFS", line)
        if m and integrated is None:
            integrated = _float(m.group(1))
            continue
        m = re.match(r"LRA:\s*(-?[0-9.]+)\s*LU", line)
        if m and lra is None:
            lra = _float(m.group(1))
            continue
        m = re.match(r"Peak:\s*(-?[0-9.]+|-inf)\s*dBFS", line)
        if m and section == "true" and true_peak is None:
            true_peak = _float(m.group(1))
    return {"integrated_lufs": integrated, "true_peak_dbtp": true_peak, "lra": lra}


# ----------------------------------------------------------------------
# Clipping: a peak sitting on the ceiling, plus astats' own flat-sample count.
# ----------------------------------------------------------------------
def build_astats_command(path):
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path,
        "-af", "astats=reset=0", "-vn", "-f", "null", "-",
    ]


def parse_astats(stderr):
    """{peak_level_db, flat_factor, peak_count} from astats. The Overall block
    is preferred; without it the worst per-channel peak wins, because clipping
    on one channel is still clipping. Pure."""
    peak = flat = None
    count = None
    overall = False
    for raw in (stderr or "").splitlines():
        line = re.sub(r"^\[[^\]]*\]\s*", "", raw).strip()
        if line.lower().startswith("overall"):
            overall = True
            peak = flat = count = None          # restart on the overall block
            continue
        m = re.match(r"Peak level dB:\s*(-?[0-9.]+|-inf|nan)", line)
        if m:
            v = _float(m.group(1))
            if v is not None and (peak is None or v > peak or overall):
                peak = v
            continue
        m = re.match(r"Flat factor:\s*(-?[0-9.]+|nan)", line)
        if m:
            v = _float(m.group(1))
            if v is not None and (flat is None or v > flat or overall):
                flat = v
            continue
        m = re.match(r"Peak count:\s*([0-9.]+)", line)
        if m:
            v = _float(m.group(1))
            if v is not None and (count is None or v > count or overall):
                count = v
    return {"peak_level_db": peak, "flat_factor": flat,
            "peak_count": int(count) if count is not None else None}


# ----------------------------------------------------------------------
# Container and stream facts.
# ----------------------------------------------------------------------
def build_probe_command(path):
    return [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", path,
    ]


def _fps(rate):
    """'30000/1001' or '25/1' as a float, or None. A 0 denominator (ffprobe's
    'no idea') is not a frame rate, so it returns None instead of raising."""
    if not rate or not isinstance(rate, str):
        return None
    try:
        num, _, den = rate.partition("/")
        n, d = float(num), float(den or 1)
        return round(n / d, 3) if d else None
    except ValueError:
        return None


def parse_probe(stdout):
    """The ffprobe JSON reduced to the facts QC actually uses. Returns {} when
    the output is unusable, so the caller treats it as 'could not measure'. Pure."""
    try:
        data = json.loads(stdout or "")
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    out = {
        "duration_sec": _float(fmt.get("duration")),
        "size_bytes": int(_float(fmt.get("size")) or 0) or None,
        "container": fmt.get("format_name"),
        "stream_count": len(streams),
        "video": None,
        "audio": None,
    }
    if video:
        out["video"] = {
            "codec": video.get("codec_name"),
            "width": int(_float(video.get("width")) or 0) or None,
            "height": int(_float(video.get("height")) or 0) or None,
            "fps": _fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
            "start_time": _float(video.get("start_time")),
            "duration_sec": _float(video.get("duration")),
        }
    if audio:
        out["audio"] = {
            "codec": audio.get("codec_name"),
            "sample_rate": int(_float(audio.get("sample_rate")) or 0) or None,
            "channels": int(_float(audio.get("channels")) or 0) or None,
            "start_time": _float(audio.get("start_time")),
            "duration_sec": _float(audio.get("duration")),
        }
    return out


def probe_media(path):
    """Container, duration and stream facts for a local file. Never raises,
    returns {} when the probe fails."""
    ok, stdout, _ = run_capture(build_probe_command(path), timeout=PROBE_TIMEOUT)
    return parse_probe(stdout) if ok else {}


# ----------------------------------------------------------------------
# Resolution / frame rate changes mid-episode: a spliced-in clip exported at a
# different size or rate. ffprobe's stream header only reports the FIRST
# picture size, so the file has to be sampled at intervals to see a change.
# ----------------------------------------------------------------------
def sample_offsets(duration_sec, samples=8):
    """Evenly spread sample points inside the file, skipping the first and last
    few seconds where a fade or a trailing frame gives a false reading. Pure."""
    try:
        dur = float(duration_sec or 0)
    except (TypeError, ValueError):
        return []
    if dur <= 0 or samples < 1:
        return []
    edge = min(5.0, dur / 10.0)
    span = dur - (edge * 2)
    if span <= 0:
        return [round(dur / 2, 2)]
    if samples == 1:
        return [round(edge + span / 2, 2)]
    step = span / (samples - 1)
    return [round(edge + step * i, 2) for i in range(samples)]


def build_frame_sample_command(path, offset_sec, frames=5):
    """ffprobe a short burst of frames starting at `offset_sec`. -read_intervals
    seeks instead of decoding from zero, so sampling a 60 minute master costs
    seconds rather than a full decode."""
    return [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-read_intervals", f"{offset_sec}%+#{frames}",
        "-show_entries", "frame=width,height,best_effort_timestamp_time",
        "-of", "json", path,
    ]


def parse_frame_samples(stdout):
    """[{time, width, height}] from a frame burst, plus the local frame rate
    implied by the gaps between the timestamps. Returns {} on unusable output.
    Pure."""
    try:
        data = json.loads(stdout or "")
        frames = data.get("frames") or []
    except (ValueError, TypeError, AttributeError):
        return {}
    dims, times = [], []
    for f in frames:
        w = int(_float(f.get("width")) or 0)
        h = int(_float(f.get("height")) or 0)
        if w and h:
            dims.append((w, h))
        t = _float(f.get("best_effort_timestamp_time"))
        if t is not None:
            times.append(t)
    if not dims:
        return {}
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    # The median gap, not the mean: one long gap at a keyframe boundary should
    # not drag the estimate. This is a LOCAL estimate, never a container fact.
    fps = None
    if gaps:
        gaps.sort()
        mid = gaps[len(gaps) // 2]
        fps = round(1.0 / mid, 2) if mid > 0 else None
    return {"time": times[0] if times else None, "width": dims[0][0],
            "height": dims[0][1], "fps": fps, "frames": len(dims)}


def detect_resolution_changes(path, duration_sec=None, samples=8):
    """Sample the file at intervals and report every point whose picture size or
    local frame rate differs from the first sample, which is what a spliced-in
    clip exported on different settings looks like. Returns [] when nothing
    changed AND when the file could not be sampled, so the caller checks
    `probe_media` separately before reading [] as 'all good'. Never raises."""
    if duration_sec is None:
        duration_sec = (probe_media(path) or {}).get("duration_sec")
    baseline, changes = None, []
    for offset in sample_offsets(duration_sec, samples):
        ok, stdout, _ = run_capture(build_frame_sample_command(path, offset),
                                    timeout=PROBE_TIMEOUT)
        sample = parse_frame_samples(stdout) if ok else {}
        if not sample:
            continue
        if baseline is None:
            baseline = sample
            continue
        size_changed = (sample["width"], sample["height"]) != (baseline["width"],
                                                               baseline["height"])
        # 1 fps of slack: the local estimate is derived from a handful of
        # timestamps, so a small wobble is measurement noise, not a splice.
        rate_changed = (sample.get("fps") and baseline.get("fps")
                        and abs(sample["fps"] - baseline["fps"]) > 1.0)
        if size_changed or rate_changed:
            changes.append({"time": offset, "width": sample["width"],
                            "height": sample["height"], "fps": sample.get("fps"),
                            "baseline_width": baseline["width"],
                            "baseline_height": baseline["height"],
                            "baseline_fps": baseline.get("fps")})
    return changes


# ----------------------------------------------------------------------
# A/V sync: picture and sound starting or ending apart.
# ----------------------------------------------------------------------
def av_sync_drift(path, probe=None):
    """{start_offset_sec, duration_delta_sec} between the video and audio
    streams, from their own start times and durations. Returns {} when either
    stream is missing or unmeasured, which the caller reports as 'not run'
    rather than as a clean result. Never raises."""
    info = probe if probe is not None else probe_media(path)
    video, audio = (info or {}).get("video"), (info or {}).get("audio")
    if not video or not audio:
        return {}
    v_start, a_start = video.get("start_time"), audio.get("start_time")
    v_dur, a_dur = video.get("duration_sec"), audio.get("duration_sec")
    out = {}
    if v_start is not None and a_start is not None:
        out["start_offset_sec"] = round(v_start - a_start, 4)
    if v_dur is not None and a_dur is not None:
        out["duration_delta_sec"] = round(v_dur - a_dur, 4)
    return out


# ----------------------------------------------------------------------
# File identity: the checksum that ties a QC result to one exact file, so a
# later re-export cannot quietly inherit a clean result.
# ----------------------------------------------------------------------
def md5_file(path, chunk_bytes=4 * 1024 * 1024):
    """Streaming md5 of a file, read in chunks so a multi-gigabyte master never
    lands in memory. Returns the hex digest, or None if the file cannot be read."""
    import hashlib
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk_bytes)
                if not block:
                    break
                h.update(block)
        return h.hexdigest()
    except OSError as e:
        print(f"      ~ qc checksum failed ({str(e)[:160]})")
        return None
