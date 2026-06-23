# KH Clipper — Brand + Caption Spec

**Date:** 2026-06-09
**Brand source:** Official KH Brand Guidelines (Marilia Glauche, 2024) + KH-BG-001
**Where this lives:** Stage 5 `caption.py` + logo overlay + audiogram mode.
**Config source of truth:** `src/brand.py` (change brand values there, nowhere else).

**Why this tool exists (KH mission):** "Help people tell the stories they need to share, so that people can find the stories they need to hear." Every clip serves the second half of that line.

---

## 1. The look in one line

Big cream captions in an olive pill, the spoken word lights up gold, KH logo top-right. On-brand, trauma-informed, readable on a phone.

---

## 2. Caption style (recommended: "Olive Pill")

| Element | Value | Brand colour |
| --- | --- | --- |
| Font | Open Sans SemiBold | brand caption font (approved weight) |
| Resting word text | Cream white | `#FFF9ED` |
| Active (spoken) word | Gold | `#ED9A1F` |
| Text outline | Dark olive, 4px | `#2D2F22` |
| Background pill | Dark olive, ~80% opacity | `#2D2F22` |
| Position | Lower third, centred | — |
| Words per line | 2 to 4 max | — |

**Why this one:** cream text on an olive pill is the highest-contrast, most readable combo and it uses the signature KH palette. Gold active-word follows the 60-30-10 rule (gold is the 30% accent). Olive sits outside 60-30-10 as the "black", so the pill does not eat the budget.

**Alt style ("Floating"):** no pill, cream text with a heavy olive outline, active word gets a small gold box behind it. More modern/Hormozi-style, slightly less readable on bright footage. Pick one (see preview).

---

## 3. The exact ASS subtitle styling

Captions are burned in with ffmpeg + libass using an ASS style. This is the style line `caption.py` writes into the generated `.ass` file:

```
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginV
Style: KHBase,Open Sans,96,&H00EDF9FF,&H001F9AED,&H00222F2D,&H66222F2D,1,3,4,0,2,360
```

Reading that:
- `PrimaryColour &H00EDF9FF` = cream resting text
- `SecondaryColour &H001F9AED` = gold (used for the active word)
- `OutlineColour &H00222F2D` = olive text edge
- `BackColour &H66222F2D` = olive pill at ~80% opacity (the `66` is alpha)
- `BorderStyle 3` = boxed text (the pill). Use `1` instead for the Floating alt.
- `Alignment 2` = bottom-centre. `MarginV 360` lifts it into the lower third.

**Word-level highlight (the karaoke part):** for each caption line, `caption.py` emits one Dialogue event per spoken word, recolouring just the current word gold with an inline override and leaving the rest cream. Example for a 3-word line where word 2 is active:

```
Dialogue: 0,0:00:01.20,0:00:01.68,KHBase,,0,0,0,,{\c&H00EDF9FF&}You {\c&H001F9AED&}are{\c&H00EDF9FF&} enough
```

This drives the highlight off the word `start`/`end` times already in `output/<id>.transcript.json` (the data contract from the handoff). No new data needed.

---

## 4. Logo overlay (ffmpeg)

Uses the approved "White on black" secondary lockup (gold mic + cream wordmark), background made transparent. Placed top-right with brand clear space, burned on after captions:

```bash
ffmpeg -i clip.mp4 -i assets/logo/KH_Logo_OnDark.png -filter_complex \
"[1]scale=324:-1[lg];[0][lg]overlay=W-w-54:54:format=auto" \
-c:a copy clip_branded.mp4
```

- `scale=324:-1` = logo is 30% of a 1080-wide frame (the secondary lockup is wide)
- `overlay=W-w-54:54` = 54px clear space from top and right edges (~height of the "K")
- On light footage, swap to colorway A (full colour on white). On dark/real footage this on-dark variant is correct.

---

## 5. Assets — DONE (already in the repo)

Both are now in place, extracted from the official guidelines and Google Fonts:

1. **Logo:** `assets/logo/KH_Logo_OnDark.png` (transparent, gold mic + cream wordmark) plus `KH_Logo_OnDark_boxed.png` (olive lozenge version) as a fallback.
2. **Fonts:** `assets/fonts/OpenSans-SemiBold.ttf` and `Archivo-SemiBold.ttf` (weights pinned to the approved SemiBold so captions render identically on any machine).

Caption render uses Open Sans (weight baked into the file), so the ASS `Bold` flag stays OFF.

---

## 6. Status + what's next

Locked and done:
- Caption style: **Olive Pill** (chosen 2026-06-09).
- Logo asset: extracted, transparent, in the repo.
- Fonts: SemiBold instances in the repo.
- `src/brand.py`: the single source of truth, fully populated.

Next: build Stage 5 `caption.py` to this spec (reads straight from `src/brand.py`). It needs clip-worthy moments to caption, so Stage 2 `detect` (find the Kintsugi moments) comes first.
