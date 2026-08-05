"""
KH Clipper — Brand Config (single source of truth)
Brand standard: KH-BG-001 + official KH Brand Guidelines (Marilia Glauche, 2024)

KH mission (the reason this tool exists):
"Help people tell the stories they need to share, so that people can find the
stories they need to hear." Every clip we cut serves the second half of that line.

Every output (captions, audiograms, logo overlay) imports from here so clips
are on-brand automatically. Change brand values in ONE place: this file.

Colour rule: 60-30-10 — 60% cream, 30% gold, 10% lime. Olive is the "black".
Fonts: Archivo (headings) + Open Sans (body/captions). Approved weights only:
Archivo (Regular, Medium, SemiBold, Bold) / Open Sans (Regular, SemiBold).
Contrast (per guidelines p24): gold-on-dark and cream-on-dark are AAA-safe for
text; lime is for large text/graphics only, never caption body text.
Tone of voice lives in the prompt layer (detect.py), not here.
"""

# ----------------------------------------------------------------------
# COLOURS  — hex (for reference) + ASS (&HAABBGGRR) for ffmpeg/libass
# ASS alpha: 00 = fully opaque, FF = fully transparent
# ----------------------------------------------------------------------
COLOURS = {
    "gold":          {"hex": "#ED9A1F", "rgb": (237, 154, 31),  "ass": "&H001F9AED"},
    "dark_olive":    {"hex": "#2D2F22", "rgb": (45, 47, 34),    "ass": "&H00222F2D"},
    "sec_olive":     {"hex": "#424530", "rgb": (66, 69, 48),    "ass": "&H00304542"},
    "cream_white":   {"hex": "#FFF9ED", "rgb": (255, 249, 237), "ass": "&H00EDF9FF"},
    "neutral_cream": {"hex": "#FFEFCD", "rgb": (255, 239, 205), "ass": "&H00CDEFFF"},
    "lime":          {"hex": "#F0FFA3", "rgb": (240, 255, 163), "ass": "&H00A3FFF0"},
}

# ----------------------------------------------------------------------
# FONTS — both free on Google Fonts. Ship the .ttf files with the repo so
# libass renders them regardless of machine. Path is relative to repo root.
# ----------------------------------------------------------------------
FONTS = {
    # family = the font's internal name (what libass matches on). These are the
    # approved SemiBold weights, re-named uniquely so libass never grabs a
    # different weight by mistake. Render with Bold OFF in the ASS style.
    "heading": {"family": "KH Heading", "file": "assets/fonts/KH-Heading.ttf"},
    "caption": {"family": "KH Caption", "file": "assets/fonts/KH-Caption.ttf"},
}

# ----------------------------------------------------------------------
# LOGO — protected asset. Use the All-White variant on video (footage is
# busy/dark). Clear space around logo = height of the "K" (handled by margin).
# ----------------------------------------------------------------------
LOGO = {
    # On video clips: the BOXED lockup (logo on a dark-olive panel) so it reads on
    # ANY footage — light walls included — where the transparent cream wordmark washes
    # out. Olive is the brand "black", so the panel stays on-brand.
    "file_on_video": "assets/logo/KH_Logo_OnDark_boxed.png",
    # On LIGHT backgrounds (audiograms / cream cards): a dark or full-colour
    # transparent lockup so the wordmark reads. Drop the file here; audiogram.py
    # falls back to the on-dark logo on an olive band if this is missing.
    "file_on_light": "assets/logo/KH_Logo_OnLight.png",
    "position": "top_right",     # top_left | top_right
    "width_pct": 0.38,           # boxed lockup carries weight; 38% of 1080 -> ~410px
    "margin_pct": 0.04,          # clear-space margin from edges
    "opacity": 0.90,             # slightly soft so the panel doesn't dominate
}

# ----------------------------------------------------------------------
# CAPTION STYLE — vertical Shorts (1080 x 1920). Word-level karaoke:
# the whole line sits in an olive pill; the active word turns gold.
# ----------------------------------------------------------------------
CAPTION = {
    "frame": (1080, 1920),
    "font": FONTS["caption"]["family"],
    "font_file": FONTS["caption"]["file"],
    "font_size": 96,             # big, mobile-legible. 2-4 words per line.
    "max_words_per_line": 4,

    "base_colour":   COLOURS["cream_white"]["ass"],   # resting word text
    "active_colour": COLOURS["gold"]["ass"],          # current spoken word
    "outline_colour": COLOURS["dark_olive"]["ass"],   # text edge for contrast
    "outline_px": 4,
    "shadow_px": 0,

    # Background "pill" that highlights the caption on screen
    "box": True,                                      # BorderStyle=3 (boxed text)
    "box_colour": COLOURS["dark_olive"]["ass"],       # olive pill
    "box_opacity": 0.80,                              # ~80% opaque
    "vertical_pos": "lower",                          # caption block placement
    "margin_v_px": 380,                               # px up from bottom — sits low in
                                                      # the bottom band (~y1540), just
                                                      # above the native Shorts UI; CTA
                                                      # end cards stack above it
}

# ----------------------------------------------------------------------
# ANIMATION — Shorts Motion Graphics Upgrade (KH-MGX-001, Wave 1).
# Every tunable for kinetic captions, face-aware placement and the gentle
# punch-in lives HERE. caption.py/reframe.py must never hardcode a timing,
# scale or threshold — they read this block.
#
# Trauma-informed rule (KH-TIC-001 alignment, locked decision #3): a clip
# whose safety rating is anything other than "ok" (i.e. "review") renders
# with the CALM preset — fades only, no pop, no scale, no zoom. Energy never
# overrides dignity. The preset lookup is `ANIMATION["presets"][name]`;
# "standard" is the default, "calm" is selected by the caller (caption.py)
# whenever the clip's safety != "ok".
# ----------------------------------------------------------------------
ANIMATION = {
    # 1.1 — word pop-in. The active (currently spoken) word animates in from
    # `pop_from_scale` to its target scale over `pop_ms`, via ASS \t + \fscx\fscy.
    "pop_ms": 120,                  # entry animation duration (ms)
    "pop_from_scale": 80,           # starting scale %, animates up to the target
    "rest_scale": 100,              # a resting (not active, not highlighted) word
    "active_scale": 108,            # the active word's target scale % while it's live
    "line_fade_ms": 60,             # \fad(N,0) on line changes (new line fades in)

    # 1.2 — emphasis word (detect.py's highlight_word). Rendered gold at this
    # scale whenever it's on screen, active or not, so the emotional core of
    # the clip visually lands. Disabled under the CALM preset.
    "highlight_scale": 115,

    # 1.3 — face-aware caption placement. reframe.py writes a per-clip
    # <clip>.faceband.json (min/max normalised y of the guest's face across
    # the WHOLE clip); caption.py picks ONE of these two bands for the whole
    # clip — never per frame, no jitter.
    "caption_bands": {
        "default_margin_v_px": 380,     # unchanged default (lower band)
        "raised_margin_v_px": 620,      # used when the face sits unusually low
        "low_face_threshold": 0.62,     # face bottom (normalised) >= this = "low"
    },
    # Same face-aware idea applied to the hook banner (upper third), checked
    # against the face TOP instead of the bottom.
    "banner_bands": {
        "default_margin_v_px": 360,     # unchanged default (matches Banner style)
        "mid_margin_v_px": 520,         # dropped position when the crop puts the face high
        "high_face_threshold": 0.20,    # face top (normalised) <= this = "high"
    },

    # 1.4 — gentle punch-in (Ken Burns zoom), optional, behind a flag. Default
    # ON for standard clips; the CALM preset always forces it off.
    "punch_in": {
        "enabled": True,
        "start_scale": 1.00,
        "end_scale": 1.04,
    },

    # 1.5 — presets. `standard` is every effect above at full energy; `calm`
    # is trauma-informed: fades only, no pop, no scale, no zoom, a longer,
    # gentler fade. Selected automatically from the clip's safety rating.
    "presets": {
        "standard": {
            "pop": True,             # word pop-in + active-word scale
            "highlight": True,       # highlight_word emphasis
            "punch_in": True,        # gentle Ken Burns zoom
            "fade_ms": 60,           # line-change fade (mirrors line_fade_ms)
        },
        "calm": {
            "pop": False,
            "highlight": False,
            "punch_in": False,
            "fade_ms": 220,          # longer, gentler fade — no hard cuts, no energy
        },
    },
}

# ----------------------------------------------------------------------
# AUDIOGRAM MODE (planned) — reuses the same palette + fonts.
# ----------------------------------------------------------------------
AUDIOGRAM = {
    "bg_colour": COLOURS["cream_white"]["hex"],       # 60% cream background
    "wave_colour": COLOURS["gold"]["hex"],            # 30% gold waveform
    "accent_colour": COLOURS["lime"]["hex"],          # 10% lime accent line
    "text_colour": COLOURS["dark_olive"]["hex"],
}

# ----------------------------------------------------------------------
# AUDIOGRAM V2 — KH-MGX-001 Wave 2. Remotion render of the KH design-suite
# audiogram card (source of truth: kh-studio's KHAudiogram.dc.html), a
# genuine visual upgrade over the Pillow frame-by-frame version
# (src/audiogram.py): a real spring-eased waveform-bar entrance, animated
# caption transitions and a data-driven progress fill, instead of every
# frame being independently drawn with no real easing between them.
#
# Per-series palette (bg/ink/accent/logo/seed) stays in src/audiogram.py's
# PALETTES table (resolved server-side, same as today) — not duplicated
# here. Everything ELSE a render needs — fonts, timing, spring config,
# per-format layout geometry — lives HERE so render-cli.mjs / the React
# component never hardcode a size, font or timing (KH-MGX-001 locked
# decision #2). Fonts match the KH design-suite's own family set
# (Archivo / Open Sans / IBM Plex Mono, per SHORTS-AUDIOGRAM-DESIGN-
# SPEC.md), distinct from ANIMATION's KH Heading / KH Caption pair used
# by the KH Kinetic caption template.
#
# Trauma-informed rule (KH-TIC-001 / KH-MGX-001 locked decision #3):
# CALM (any clip whose safety != "ok") disables the spring-eased bar
# entrance and the progress-fill glow — fades only, no pop, no bounce.
# The reactive waveform bar HEIGHT itself (driven by the clip's real
# voice) and the linear progress fill stay ACTIVE under CALM in both
# presets — they are a continuous, non-jarring representation of the
# audio/playback position, the entire point of an audiogram, not a
# decorative flourish; src/audiogram.py already renders that reactive
# waveform identically for every clip regardless of safety rating, with
# no complaint raised against it. Only the ENTRANCE motion and the
# celebratory progress-glow are gated by the preset.
# ----------------------------------------------------------------------
AUDIOGRAM_V2 = {
    "fonts": {
        # Re-named uniquely (matching brand.py's own convention for the
        # Kinetic fonts above) so the render never accidentally grabs a
        # different installed weight.
        "heading_bold":  {"family": "KH Audio Heading",       "file": "assets/fonts/Archivo-Bold.ttf"},
        "heading_xbold": {"family": "KH Audio Heading XBold", "file": "assets/fonts/Archivo-ExtraBold.ttf"},
        "body":          {"family": "KH Audio Body",          "file": "assets/fonts/OpenSans.ttf"},
        "mono":          {"family": "KH Audio Mono",          "file": "assets/fonts/IBMPlexMono-Regular.ttf"},
    },

    # Waveform bars — count matches src/audiogram.py's NBARS exactly, so
    # the seeded-envelope fallback below (used when a clip has no usable
    # audio) is pixel-for-pixel the same shape as the Pillow version.
    "bars": {
        "count": 44,
        "min_scale": 0.34,          # matches audiogram.py's floor exactly
        "entrance_ms": 700,         # whole-row spring entrance duration
        "entrance_stagger_ms": 12,  # per-bar stagger (44 * 12ms ~= 530ms sweep, left to right)
        "spring_damping": 16,
        "spring_mass": 0.7,
        "calm_fade_ms": 320,        # CALM: no spring pop, the row fades in instead
    },

    # Progress bar — true 0->100% fill over the clip's real duration
    # (same as audiogram.py); the "glow" is a purely decorative pulse at
    # completion, standard preset only.
    "progress": {
        "fill_glow_ms": 260,
    },

    # Timed caption chunks (src/audiogram.py's group_caption_lines shape)
    # cross-fade between each other; the static single-caption mode uses
    # the same fade-in-once timing.
    "caption_transition": {
        "fade_ms": 220,
        "calm_fade_ms": 420,        # CALM: a longer, gentler fade, no hard cut
    },

    "presets": {
        "standard": {"bar_entrance_spring": True,  "progress_glow": True},
        "calm":     {"bar_entrance_spring": False, "progress_glow": False},
    },

    # Per-format layout geometry, ported 1:1 from src/audiogram.py's
    # _build_static()/_render() constants (wide=landscape, tall=vertical,
    # everything else=square) so a Remotion render lands on the exact same
    # proportions the Pillow version already established. Every value is
    # in cqw units (percent of min(width, height)/100), matching
    # audiogram.py's own `cqw = min(W, H) / 100.0` convention.
    "layout": {
        "wide": {   # landscape, 16:9 — the KH-VRL-001 promo-video format
            "pad_x": 6.0, "pad_y": 6.0,
            "logo_h": 7.5, "eyebrow_size": 2.2, "caption_size": 4.0,
            "waveform_h": 22.0, "bar_w": 2.2, "bar_gap": 1.1,
            "caption_max_w_frac": 0.72,  # fraction of the FULL frame width, not cqw
        },
        "tall": {   # vertical, 9:16
            "pad_x": 7.0, "pad_y": 8.0,
            "logo_h": 12.0, "eyebrow_size": 3.8, "caption_size": 5.4,
            "waveform_h": 34.0, "bar_w": 1.4, "bar_gap": 0.7,
            "caption_max_w_frac": None,  # uses caption_max_w_cqw below
            "caption_max_w_cqw": 84.0,
        },
        "square": {
            "pad_x": 8.0, "pad_y": 8.0,
            "logo_h": 8.6, "eyebrow_size": 2.5, "caption_size": 4.4,
            "waveform_h": 24.0, "bar_w": 1.4, "bar_gap": 0.7,
            "caption_max_w_frac": None,
            "caption_max_w_cqw": 84.0,
        },
        "bar_radius": 0.7,     # cqw, same in every format
        "centre_gap": 5.0,     # cqw, gap between waveform row and caption
        "progress_h": 0.7,     # cqw
        "progress_gap": 3.5,   # cqw, gap between progress bar and footer text
    },
}

# ----------------------------------------------------------------------
# CALL-TO-ACTION overlays — grow the channel, gently and on-brand.
# Two variants: "shorts" (arrows point at YouTube's native buttons) and
# "universal" (branded text only, for Reels/TikTok where buttons differ).
# Style: olive pill, cream text, gold arrows/emphasis. Archivo SemiBold.
# ----------------------------------------------------------------------
CTA = {
    "font": FONTS["heading"]["family"],          # Archivo SemiBold
    "font_size": 72,                             # larger, more legible CTA pills
    "pill_colour": COLOURS["dark_olive"]["ass"],
    "pill_opacity": 0.85,
    "text_colour": COLOURS["cream_white"]["ass"],
    "accent_colour": COLOURS["gold"]["ass"],     # arrows + emphasis word

    # Copy (KH voice, Australian English, gentle, no hype words).
    "copy": {
        "subscribe_soft": "Subscribe for more real stories",
        "subscribe": "Don't forget to subscribe",
        "full_episode": "Listen to the full episode",
        "related": "Full episode in the linked video below",
        "handle": "@kintsugiheroes",
    },

    # Native YouTube Shorts UI targets on a 1080x1920 frame (approx — tune these
    # after checking on a real device; YouTube moves them between app versions).
    "shorts_targets": {
        "subscribe_btn":   [430, 1715],   # the Subscribe pill, bottom-left area
        "channel_profile": [150, 1715],   # avatar + @handle (tap = go to channel)
        "related_link":    [300, 1830],   # linked/related video banner, very bottom
    },

    # Timing (gentle rotating). Soft nudge early, clean middle, stronger end cards.
    "soft_window": [2.0, 6.0],            # early subscribe nudge (s)
    "end_window_sec": 6.0,                # last N secs hold the end cards
    "soft_y": 300,                        # early nudge sits high, clear of captions
    "fade_ms": 300,
}
