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
