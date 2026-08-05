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

    # Wave 2 — KH End Screen (Remotion only; the libass path has its own,
    # separate always-on CTA system, src/cta.py, unaffected by this). An
    # animated CTA overlay over the FINAL SECONDS of the clip — NOT appended
    # after (that is the retired src/endscreen.py pattern); the overlay
    # occupies the tail of the composition's own existing duration, so total
    # duration is unaffected (unlike quote_card_intro, which prepends and
    # genuinely extends it). `enabled` is a GLOBAL kill-switch (same role as
    # punch_in's/quote_card_intro's). Unlike quote_card_intro, the per-clip
    # choice defaults ON (`src.kinetic.finish(end_screen_cta=True)`) so the
    # kinetic style stays at parity with classic's own always-on CTA instead
    # of being a downgrade — see KH-MGX-001 Wave 2 End Screen PR body for the
    # reasoning.
    "end_screen": {
        "enabled": True,
        "window_sec": 3.0,      # tail window the overlay occupies ("final ~2-3 seconds")
        "stagger_ms": 500,      # delay before the 2nd message group pops in after the 1st
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
# CALL-TO-ACTION overlays — grow the channel, gently and on-brand.
# Two variants: "shorts" (arrows point at YouTube's native buttons) and
# "universal" (branded text only, for Reels/TikTok where buttons differ).
# Style: olive pill, cream text, gold arrows/emphasis. Archivo SemiBold.
#
# Reused verbatim (copy + shorts_targets) by the Wave 2 KH End Screen
# Remotion template (src/kinetic.py, ANIMATION["end_screen"] above) so the
# kinetic and classic CTAs stay on the same message and point at the same
# native-UI positions — export_brand.py exports the fields below as-is,
# never a second copy.
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
