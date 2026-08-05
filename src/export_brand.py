"""
KH Clipper — Wave 2 (KH-MGX-001): export src/brand.py to JSON for the Remotion
render layer, so Python and React can never drift on colours, fonts or timings.
brand.py stays the single source of truth; this script is the ONLY thing that
reads it and writes render/brand.json (or a path you choose). Run it before a
kinetic render, or whenever brand.py changes.

    python -m src.export_brand [output_path]   # default: render/brand.json
"""
from __future__ import annotations
import json
import os
import sys
from src import brand

DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "render", "brand.json"
)


def build() -> dict:
    """Curated, camelCase, WEB-friendly shape (hex colours, plain px/ms/percent
    numbers) — never the ASS-specific values (&HAABBGGRR, ASS margin
    conventions) those are libass-only. Every field here is read by
    render/src/KHKinetic.tsx; nothing on the React side is hardcoded."""
    c = brand.COLOURS
    anim = brand.ANIMATION

    def preset(name):
        p = anim["presets"][name]
        return {
            "pop": p["pop"],
            "highlight": p["highlight"],
            "punchIn": p["punch_in"],
            "fadeMs": p["fade_ms"],
        }

    return {
        "colours": {
            "gold": c["gold"]["hex"],
            "darkOlive": c["dark_olive"]["hex"],
            "secOlive": c["sec_olive"]["hex"],
            "creamWhite": c["cream_white"]["hex"],
            "neutralCream": c["neutral_cream"]["hex"],
            "lime": c["lime"]["hex"],
        },
        "fonts": {
            "headingFamily": brand.FONTS["heading"]["family"],
            "headingFile": brand.FONTS["heading"]["file"],
            "captionFamily": brand.FONTS["caption"]["family"],
            "captionFile": brand.FONTS["caption"]["file"],
        },
        "caption": {
            "fontSize": brand.CAPTION["font_size"],
            "maxWordsPerLine": brand.CAPTION["max_words_per_line"],
            "outlinePx": brand.CAPTION["outline_px"],
            "boxOpacity": brand.CAPTION["box_opacity"],
        },
        "animation": {
            "popMs": anim["pop_ms"],
            "popFromScale": anim["pop_from_scale"],
            "restScale": anim["rest_scale"],
            "activeScale": anim["active_scale"],
            "lineFadeMs": anim["line_fade_ms"],
            "highlightScale": anim["highlight_scale"],
            "captionBands": {
                "defaultMarginVPx": anim["caption_bands"]["default_margin_v_px"],
                "raisedMarginVPx": anim["caption_bands"]["raised_margin_v_px"],
                "lowFaceThreshold": anim["caption_bands"]["low_face_threshold"],
            },
            "bannerBands": {
                "defaultMarginVPx": anim["banner_bands"]["default_margin_v_px"],
                "midMarginVPx": anim["banner_bands"]["mid_margin_v_px"],
                "highFaceThreshold": anim["banner_bands"]["high_face_threshold"],
            },
            "punchIn": {
                "enabled": anim["punch_in"]["enabled"],
                "startScale": anim["punch_in"]["start_scale"],
                "endScale": anim["punch_in"]["end_scale"],
            },
            "endScreen": {
                "enabled": anim["end_screen"]["enabled"],
                "windowSec": anim["end_screen"]["window_sec"],
                "staggerMs": anim["end_screen"]["stagger_ms"],
            },
            "presets": {
                "standard": preset("standard"),
                "calm": preset("calm"),
            },
        },
        # Wave 2 — KH End Screen reuses brand.CTA verbatim (never a second copy
        # of the copy/targets already locked for the classic libass CTA,
        # src/cta.py). Colours are NOT re-exported here — CTA's pill/text/
        # accent colours are the same darkOlive/creamWhite/gold already in
        # `colours` above, so the render layer reads those directly.
        "cta": {
            "copy": {
                "subscribeSoft": brand.CTA["copy"]["subscribe_soft"],
                "subscribe": brand.CTA["copy"]["subscribe"],
                "fullEpisode": brand.CTA["copy"]["full_episode"],
                "related": brand.CTA["copy"]["related"],
                "handle": brand.CTA["copy"]["handle"],
            },
            "shortsTargets": {
                "subscribeBtn": brand.CTA["shorts_targets"]["subscribe_btn"],
                "channelProfile": brand.CTA["shorts_targets"]["channel_profile"],
                "relatedLink": brand.CTA["shorts_targets"]["related_link"],
            },
            "pillOpacity": brand.CTA["pill_opacity"],
            "fontSize": brand.CTA["font_size"],
        },
    }


def export_brand(out_path: str | None = None) -> str:
    out_path = out_path or DEFAULT_OUT
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(build(), f, indent=2)
    return out_path


def main():
    out = export_brand(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"brand.json -> {out}")


if __name__ == "__main__":
    main()
