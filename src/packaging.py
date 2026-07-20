"""
KH Shorts packaging standard (locked May 2026 v5). The worker's mirror of the
app's single source of truth (kh-studio lib/studio/shorts-packaging.ts). The two
sides MUST agree: the blurbs, the About-KH block, the 15-hashtag locked order, the
3-group tags field, the pinned-comment format and the cadence are byte-identical
here and in the app, and the shared spec lives in SHORTS-WORKER-CONTRACT.md.

This module owns the deterministic scaffolding only. The Grok call in metadata.py
fills the creative slots (the hook, the SEO line, the context, the topic picks,
the pinned question); this assembles the locked package around them, so a Short
packaged by the worker matches one packaged in-app for the same episode.

House voice is the floor: plain Australian English, NO em/en dashes, none of the
banned words (journey, inspiring, amazing, powerful, resilient).
"""

# Canonical cadence (matches the app; fortnightly is the deliberate later fix).
CADENCE = "fortnightly"

# Verbatim About-KH blurb (skill part 4), em dashes removed.
ABOUT_KH = (
    "Kintsugi Heroes is a not-for-profit Australian podcast network sharing real "
    "stories of resilience, transformation, and lived experience. Inspired by the "
    "Japanese art of kintsugi, repairing broken pottery with gold, we believe our "
    "cracks are what make us stronger."
)

# The 8 worker artwork slugs are the canonical key set. name / tag / blurb match
# the app's SHORTS_SERIES exactly.
SERIES = {
    "kintsugi-heroes": {
        "name": "Kintsugi Heroes",
        "tag": "#KintsugiHeroes",
        "blurb": (
            "The flagship Kintsugi Heroes feed, real stories of resilience, "
            "transformation, and lived experience from across Australia."
        ),
    },
    "animals-and-us": {
        "name": "Animals & Us",
        "tag": "#AnimalsAndUs",
        "blurb": (
            "Hosted by Natalie Stockdale, Animals & Us explores the deep bond between "
            "humans and animals, and how that connection shapes healing, identity, and "
            "purpose."
        ),
    },
    "grit-diaries": {
        "name": "Grit Diaries",
        "tag": "#GritDiaries",
        "blurb": (
            "Hosted by Simone Allan and Maryan Bova, Grit Diaries shares unfiltered "
            "stories of women who have walked through fire and come out stronger."
        ),
    },
    "golden-threads": {
        "name": "Golden Threads",
        "tag": "#GoldenThreads",
        "blurb": (
            "Golden Threads shares stories from people living with disability, the "
            "truth of the experience, not the polished version."
        ),
    },
    "connecting-seniors": {
        "name": "Connecting Seniors",
        "tag": "#ConnectingSeniors",
        "blurb": (
            "Connecting Seniors honours the lived wisdom of older Australians, sharing "
            "the stories that shaped them and the lessons that still matter."
        ),
    },
    "alpine-series": {
        "name": "Alpine Series",
        "tag": "#AlpineSeries",
        "blurb": (
            "The Alpine Series shares stories from Australia's high country, the "
            "people, the landscape, and the resilience that lives there."
        ),
    },
    "australian-carers": {
        "name": "Australian Carers",
        "tag": "#AustralianCarers",
        "blurb": (
            "Australian Carers shares the stories of the people who care for others, "
            "the unseen work, the quiet toll, and what keeps them going."
        ),
    },
    "river-murray-recovery-stories": {
        "name": "River Murray Recovery",
        "tag": "#RiverMurrayRecovery",
        "blurb": (
            "River Murray Recovery Stories captures the voices of communities along "
            "the Murray rebuilding after natural disaster, flood, fire, and beyond."
        ),
    },
}

_DEFAULT = SERIES["kintsugi-heroes"]

# Locked topic vocabulary (skill position 3 + 10-12): lowercase -> canonical tag.
TOPIC_TAGS = {
    "recovery": "#recovery", "addiction": "#addiction", "grief": "#grief",
    "disability": "#disability", "resilience": "#resilience", "mentalhealth": "#mentalhealth",
    "sobriety": "#sobriety", "trauma": "#trauma", "healing": "#healing", "ptsd": "#PTSD",
    "anxiety": "#anxiety", "depression": "#depression", "neurodiversity": "#neurodiversity",
    "adhd": "#ADHD", "autism": "#autism", "chronicillness": "#chronicillness",
    "carer": "#carer", "lossandgrief": "#lossandgrief", "vulnerability": "#vulnerability",
    "dyslexia": "#dyslexia",
}

BOOST_OPEN = ["#Shorts", "#ytshorts"]              # positions 1-2
BOOST_REMAINDER = ["#viralshorts", "#shortsvideo", "#shortsfeed"]  # 4-6
BRAND_PODCAST_TAG = "#KintsugiHeroesPodcast"       # position 9
# Audience tags (skill 13-15). "#inspiringstories" is excluded (banned word).
AUDIENCE_TAGS = [
    "#realstories", "#livedexperience", "#australianpodcast",
    "#truestories", "#storytelling", "#podcastclips", "#podcast",
]

BROAD_TAGS_LOCKED = ["shorts", "ytshorts", "viral shorts", "shorts viral"]
BROAD_TAGS_FILL = [
    "podcast clips", "podcast shorts", "real stories", "true stories", "motivational stories",
]

TITLE_SUFFIX = " | Kintsugi Heroes Shorts"
TITLE_MAX = 100
TAGS_LIMIT = 500

FULL_EPISODE_TODO = "→ PASTE FULL EPISODE LINK"
PODCAST_TODO = "→ ADD SPOTIFY / APPLE LINK"


def series_meta(slug):
    return SERIES.get(slug) or _DEFAULT


def _clean_hashtag(raw):
    tag = "#" + str(raw or "").lstrip("#").strip()
    tag = "".join(tag.split())
    return tag if len(tag) > 1 else ""


def canonical_topic_tag(raw):
    key = "".join(str(raw or "").lstrip("#").split()).lower()
    if not key:
        return ""
    return TOPIC_TAGS.get(key) or _clean_hashtag(raw)


def _dedupe_ci(tags):
    seen, out = set(), []
    for t in tags:
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def compose_hashtags(slug, primary_topic, secondary_topics=None, audience_tags=None):
    """The locked 15-hashtag block in the locked order (matches the app)."""
    meta = series_meta(slug)
    primary = canonical_topic_tag(primary_topic) or "#realstories"
    secondary = [t for t in (canonical_topic_tag(x) for x in (secondary_topics or [])) if t]
    audience = [t for t in (_clean_hashtag(x) for x in (audience_tags or [])) if t]
    ordered = (
        BOOST_OPEN
        + [primary]
        + BOOST_REMAINDER
        + ["#KintsugiHeroes", meta["tag"], BRAND_PODCAST_TAG]
        + secondary
        + audience
        + AUDIENCE_TAGS  # backfill to 15
    )
    cleaned = [t for t in (_clean_hashtag(x) for x in ordered) if t]
    return _dedupe_ci(cleaned)[:15]


def _tag_cost(tag):
    return len(tag) + (2 if any(c.isspace() for c in tag) else 0)


def compose_tags(slug, hero_name=None, primary_topic=None, post_specific=None):
    """The 3-group tags FIELD (Post Specific -> Niche -> Broad), under 500 chars."""
    def clean(s):
        return " ".join(str(s or "").lstrip("#").split()).strip().lower()

    meta = series_meta(slug)
    primary = clean(primary_topic) or "real stories"

    raw_post = ([hero_name.strip()] if hero_name else []) + [
        str(t).strip() for t in (post_specific or []) if t
    ]
    post = [t.lstrip("#").strip() for t in raw_post if t and t.strip()]
    niche = [
        "kintsugi heroes podcast",
        "kintsugi heroes shorts",
        f"{meta['name'].lower()} podcast",
        f"australian {primary} podcast",
        "lived experience stories",
        "kintsugi podcast clips",
    ]
    broad = list(BROAD_TAGS_LOCKED) + list(BROAD_TAGS_FILL)

    seen = set()

    def keep(arr):
        out = []
        for t in arr:
            k = clean(t)
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(t)
        return out

    g_post, g_niche, g_broad = keep(post), keep(niche), keep(broad)
    protected_broad = g_broad[: len(BROAD_TAGS_LOCKED)]
    trim_niche = list(g_niche)
    trim_broad_fill = g_broad[len(BROAD_TAGS_LOCKED):]
    trim_post = list(g_post)

    def total():
        return sum(_tag_cost(t) for t in (trim_post + trim_niche + protected_broad + trim_broad_fill))

    while total() > TAGS_LIMIT and trim_broad_fill:
        trim_broad_fill.pop()
    while total() > TAGS_LIMIT and trim_niche:
        trim_niche.pop()
    while total() > TAGS_LIMIT and len(trim_post) > 1:
        trim_post.pop()

    groups = {"post_specific": trim_post, "niche": trim_niche, "broad": protected_broad + trim_broad_fill}
    flat = groups["post_specific"] + groups["niche"] + groups["broad"]
    return {"groups": groups, "flat": flat}


def compose_description(slug, hook_seo_line, context, hashtags, full_episode_url=None, podcast_url=None):
    """The locked 7-part description. Main feed omits the duplicate About block."""
    meta = series_meta(slug)
    is_main_feed = meta is SERIES["kintsugi-heroes"]
    full_url = (full_episode_url or "").strip() or FULL_EPISODE_TODO
    podcast = (podcast_url or "").strip() or PODCAST_TODO

    parts = [
        (hook_seo_line or "").strip(),
        (context or "").strip(),
        f"🎙️ Watch the full episode: {full_url}\n🎧 Listen on Spotify/Apple: {podcast}",
        f"About Kintsugi Heroes:\n{ABOUT_KH}",
    ]
    if not is_main_feed:
        parts.append(f"About {meta['name']}:\n{meta['blurb']}")
    parts.append(f"🌐 Website: kintsugiheroes.com.au\n🔔 Subscribe for new episodes {CADENCE}")
    parts.append(" ".join(hashtags))
    return "\n\n".join(p for p in parts if p and p.strip()).strip()


def compose_title(hook, hero_name=None):
    """[Hook] | [Hero] | Kintsugi Heroes Shorts, capped at 100 chars."""
    hook = " ".join(str(hook or "").split()).strip().rstrip("|").strip()
    hero = (hero_name or "").strip()
    with_hero = f"{hook} | {hero}{TITLE_SUFFIX}" if hero else f"{hook}{TITLE_SUFFIX}"
    if len(with_hero) <= TITLE_MAX:
        return with_hero
    no_hero = f"{hook}{TITLE_SUFFIX}"
    if len(no_hero) <= TITLE_MAX:
        return no_hero
    room = max(0, TITLE_MAX - len(TITLE_SUFFIX))
    return f"{hook[:room].strip()}{TITLE_SUFFIX}"


def compose_pinned(question, full_episode_url=None, prefix=None):
    url = (full_episode_url or "").strip() or FULL_EPISODE_TODO
    lines = [f"🎙️ Full episode here → {url}"]
    if prefix and prefix.strip():
        lines.append(prefix.strip())
    if question and question.strip():
        lines.append(question.strip())
    return "\n\n".join(lines)
