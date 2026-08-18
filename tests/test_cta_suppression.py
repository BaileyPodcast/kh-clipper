"""
CTA end cards suppressed on loopable short clips (loop seam stays a clean hard
cut). Pure ASS-string / config assertions, no ffmpeg.

    python -m pytest tests/test_cta_suppression.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import brand, cta


def test_suppress_rule_is_loopable_and_under_25s():
    assert cta.suppress_end_cards(20.0, True)
    assert not cta.suppress_end_cards(20.0, False)      # not loopable -> cards stay
    assert not cta.suppress_end_cards(25.0, True)       # at the cap -> cards stay
    assert not cta.suppress_end_cards(30.0, True)       # long loop -> cards stay


# Accent-word wrapping inserts colour tags mid-copy, so presence checks use
# fragments that stay contiguous in the ASS event text.
CARD_A_MARK = "Don't forget"                                 # Card A opener
CARD_B_MARK = brand.CTA["copy"]["related"]                   # no accent word, contiguous
SOFT_MARK = "for more real stories"                          # soft-nudge tail


def test_loopable_short_clip_has_no_end_cards():
    events = cta.build_cta_events(20.0, "shorts", loopable=True)
    joined = "\n".join(events)
    assert CARD_A_MARK not in joined                         # Card A gone
    assert CARD_B_MARK not in joined                         # Card B gone
    # The soft early nudge is unchanged (clip > 7s, no banner suppression here).
    assert SOFT_MARK in joined


def test_non_loopable_clip_keeps_end_cards():
    events = cta.build_cta_events(20.0, "shorts", loopable=False)
    joined = "\n".join(events)
    assert CARD_A_MARK in joined
    assert CARD_B_MARK in joined


def test_long_loopable_clip_keeps_end_cards():
    events = cta.build_cta_events(30.0, "shorts", loopable=True)
    joined = "\n".join(events)
    assert CARD_B_MARK in joined


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
