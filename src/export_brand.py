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


def _build_audiogram_v2() -> dict:
    """Curated, camelCase export of brand.AUDIOGRAM_V2 — read by
    render/src/KHAudiogramV2.tsx via brand.json's `audiogramV2` key. Kept as
    its own function (rather than inlined into build()) so the two templates'
    export logic stay easy to read/test independently."""
    av2 = brand.AUDIOGRAM_V2
    fonts = av2["fonts"]
    bars = av2["bars"]
    progress = av2["progress"]
    cap = av2["caption_transition"]
    layout = av2["layout"]

    def font(key):
        f = fonts[key]
        return {"family": f["family"], "file": f["file"]}

    def preset(name):
        p = av2["presets"][name]
        return {
            "barEntranceSpring": p["bar_entrance_spring"],
            "progressGlow": p["progress_glow"],
        }

    def format_layout(key):
        f = layout[key]
        out = {
            "padX": f["pad_x"], "padY": f["pad_y"],
            "logoH": f["logo_h"], "eyebrowSize": f["eyebrow_size"],
            "captionSize": f["caption_size"], "waveformH": f["waveform_h"],
            "barW": f["bar_w"], "barGap": f["bar_gap"],
        }
        if f.get("caption_max_w_frac") is not None:
            out["captionMaxWFrac"] = f["caption_max_w_frac"]
        if f.get("caption_max_w_cqw") is not None:
            out["captionMaxWCqw"] = f["caption_max_w_cqw"]
        return out

    return {
        "fonts": {
            "headingBold": font("heading_bold"),
            "headingXBold": font("heading_xbold"),
            "body": font("body"),
            "mono": font("mono"),
        },
        "bars": {
            "count": bars["count"],
            "minScale": bars["min_scale"],
            "entranceMs": bars["entrance_ms"],
            "entranceStaggerMs": bars["entrance_stagger_ms"],
            "springDamping": bars["spring_damping"],
            "springMass": bars["spring_mass"],
            "calmFadeMs": bars["calm_fade_ms"],
        },
        "progress": {"fillGlowMs": progress["fill_glow_ms"]},
        "captionTransition": {"fadeMs": cap["fade_ms"], "calmFadeMs": cap["calm_fade_ms"]},
        "presets": {"standard": preset("standard"), "calm": preset("calm")},
        "layout": {
            "wide": format_layout("wide"),
            "tall": format_layout("tall"),
            "square": format_layout("square"),
            "barRadius": layout["bar_radius"],
            "centreGap": layout["centre_gap"],
            "progressH": layout["progress_h"],
            "progressGap": layout["progress_gap"],
        },
    }


def build() -> dict:
    """Curated, camelCase, WEB-friendly shape (hex colours, plain px/ms/percent
    numbers) — never the ASS-specific values (&HAABBGGRR, ASS margin
    conventions) those are libass-only. Every field here is read by
    render/src/KHKinetic.tsx (top-level keys) or render/src/KHAudiogramV2.tsx
    (`audiogramV2`); nothing on the React side is hardcoded."""
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
            "quoteCardIntro": {
                "enabled": anim["quote_card_intro"]["enabled"],
                "durationSec": anim["quote_card_intro"]["duration_sec"],
                "driftPx": anim["quote_card_intro"]["drift_px"],
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
        "audiogramV2": _build_audiogram_v2(),
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
