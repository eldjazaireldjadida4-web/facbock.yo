"""texttools.py — تنظيف العناوين والوسوم والوصف العام، معزول عن التطبيق الرئيسي.

الاستيراد آمن: لا قراءة إعدادات ولا نداءات شبكة عند import.
"""

import re
import unicodedata


def clean_title(title, page_name="", remove_hashtags=False):
    """ينظّف العنوان مع الإبقاء على إشارة المصدر؛ لا يخفي مصدر المقطع."""
    return build_title(title, page_name, remove_hashtags, anonymize=False)


# ════════════════════════════════════════════════════
# v4.9 — تنظيف النص + إسناد المصدر + تصنيف أخطاء الرفع + التحقق + تغليف المقطع
# ════════════════════════════════════════════════════
INVALID_TITLE_CHARS = re.compile(r"[<>\x00-\x08\x0b-\x1f\x7f-\x9f]")
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w+")
HASHTAG_RE = re.compile(r"#\w+")  # retained for compatibility; Unicode-aware removal uses _remove_hashtags.
BIDI_OVERRIDE_CHARS = str.maketrans("", "", "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# ضجيج إحصائي فيسبوكي يتسلل إلى العناوين (يشمل reactions التي كانت تُفلت).
JUNK_STATS = re.compile(
    r"\b\d[\d.,]*\s*[kKmMbB]?\s*"
    r"(views?|مشاهدة|مشاهدات|likes?|إعجاب|تفاعل|تفاعلات|reactions?|comments?|تعليق|تعليقات|"
    r"shares?|مشاركة|مشاركات|followers?|متابع|متابعين)\b",
    re.IGNORECASE,
)
EDGE_SEPARATORS = " \t·•|«»\"'\\-—_:؛,.…"


def _is_unicode_word_char(char):
    return char == "_" or char.isalnum() or unicodedata.category(char) in {"Mn", "Mc", "Me"}


def _remove_hashtags(text):
    """يحذف الوسم العربي كاملاً بما فيه الحركات المركبة، دون ابتلاع علامات الترقيم."""
    output = []
    index = 0
    while index < len(text):
        if text[index] == "#":
            end = index + 1
            has_base = False
            while end < len(text) and _is_unicode_word_char(text[end]):
                has_base = has_base or text[end] == "_" or text[end].isalnum()
                end += 1
            if has_base:
                index = end
                continue
        output.append(text[index])
        index += 1
    return "".join(output)


def _unicode_word_tokens(text):
    """يعيد كلمات Unicode مع إرفاق علامات التشكيل بالكلمة السابقة."""
    words = []
    current = []
    for char in text:
        category = unicodedata.category(char)
        if char == "_" or char.isalnum():
            current.append(char)
        elif category in {"Mn", "Mc", "Me"} and current:
            current.append(char)
        else:
            if current:
                words.append("".join(current))
                current = []
    if current:
        words.append("".join(current))
    return words


def _name_variants(name):
    raw = (name or "").strip()
    if not raw:
        return set()
    variants = {raw, raw.replace(" ", ""), raw.replace("_", " ")}
    return {v.strip() for v in variants if v.strip()}


def sanitize_public_text(text, page_name="", remove_hashtags=False,
                         remove_mentions=False, keep_newlines=False):
    """ينظف النصوص العامة والروابط؛ إزالة اسم المصدر اختيارية ولا تُفعّل افتراضياً."""
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    else:
        text = str(text)
    text = unicodedata.normalize("NFC", text).translate(BIDI_OVERRIDE_CHARS)
    text = URL_RE.sub(" ", text)
    text = JUNK_STATS.sub(" ", text)
    if remove_hashtags:
        text = _remove_hashtags(text)
    if remove_mentions:
        text = MENTION_RE.sub(" ", text)
    text = INVALID_TITLE_CHARS.sub(" ", text)

    names = page_name if isinstance(page_name, (list, tuple, set)) else [page_name]
    for name in names:
        for variant in _name_variants(name):
            pattern = r"(?<!\w)" + re.escape(variant) + r"(?!\w)"
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    if keep_newlines:
        text = "\n".join(
            re.sub(r"[ \t]+", " ", line).strip(EDGE_SEPARATORS) for line in text.splitlines()
        )
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text.strip(EDGE_SEPARATORS).strip()


def _arabic_plural(n, one, few, many):
    """صياغة عربية صحيحة للأعداد: 1 → one، 2 → few، 3-10 → few، 11+ → many."""
    if n == 1:
        return one
    if 2 <= n <= 10:
        return few
    return many


def _truncate_smart(text, limit=100):
    text = unicodedata.normalize("NFC", str(text or ""))
    limit = max(0, int(limit))
    if limit == 0:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut[:-1]:
        cut = cut.rsplit(" ", 1)[0]
    cut = cut.rstrip(" .،,;:-—|·")
    return cut or text[:limit]


SYMBOL_RUN_RE = re.compile(r"([^\w\s\u0600-\u06FF])\1{1,}")


def _collapse_symbol_runs(text):
    """يطوي تكرارات الرموز والعلامات المتتالية (‏!!! → !، 🔥🔥🔥 → 🔥) لإحكام العنوان."""
    text = re.sub(r"\.{3,}", "\u2026", text)
    return SYMBOL_RUN_RE.sub(r"\1", text)


def _drop_truncated_brand(final_text, raw_text):
    """إذا كان عنوان المصدر مقطوعاً (ينتهي بـ...) نحذف مقطع العلامة الأخير
    (مثل «| Elwatania T») لأنه غير مكتمل ولا يمكن الثقة به."""
    if not re.search(r"(?:\.{3}|…|\.\.\s*)\s*$", (raw_text or "").strip()):
        return final_text
    return re.sub(r"\s*\|[^|]*$", "", final_text).strip(" -|،,.")


def build_title(title, page_name="", remove_hashtags=False, anonymize=False):
    """ينظف العنوان، مع حذف اسم المصدر فقط عند طلب ذلك صراحة."""
    text = sanitize_public_text(title, page_name if anonymize else "",
                                remove_hashtags=remove_hashtags)
    if anonymize:
        text = _drop_truncated_brand(text, title)
        text = re.sub(r"\s*[|·•]\s*(?=[|·•#])", " ", text)
    text = _collapse_symbol_runs(text)
    return _truncate_smart(text, 100) or "Facebook Reel"


def prepare_public_title(title, source_names=(), remove_hashtags=False,
                         omit_source_page_name=True):
    result = build_title(
        title,
        source_names,
        remove_hashtags=remove_hashtags,
        anonymize=omit_source_page_name,
    )
    if omit_source_page_name:
        result = re.sub(r"\s*[|·•]+\s*", " ", result).strip()
        result = re.sub(r"\s{2,}", " ", result)
    if omit_source_page_name and (result == "Facebook Reel" or not result):
        return "مقطع فيديو"
    return result




YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESCRIPTION_MAX = 5000
YOUTUBE_TAGS_MAX = 500
# كلمات حشو شائعة في عناوين فيسبوك لا فائدة منها في يوتيوب
FILLER_PATTERNS = [
    r'\bريلز\b', r'\breels?\b', r'\bفيديو\s*جديد\b', r'\bشاهد\s*(الآن|الفيديو)?\b',
    r'\bلا\s*تفوت\w*\b', r'\bحصريا\b', r'\bعاجل\s*:\s*', r'📹|🎥|🎬|▶️|🔴',
]

def optimize_title(raw_title, page_name="", duration=0, prefix="", suffix="",
                   auto_shorts=True, remove_hashtags=True):
    """يحوّل عنوان فيسبوك الخام إلى عنوان يوتيوب نظيف وجذاب.

    الخطوات: تنظيف → إزالة الحشو → إزالة الكلمات المكررة → قالب بادئة/لاحقة
    → وسم #Shorts تلقائياً للمقاطع القصيرة → قصّ عند حد يوتيوب.
    """
    title = clean_title(raw_title, page_name, remove_hashtags)
    if title != "Facebook Reel":  # البديل الافتراضي لا تُزال منه كلمات الحشو
        for pat in FILLER_PATTERNS:
            title = re.sub(pat, ' ', title, flags=re.IGNORECASE)
        # إزالة الكلمات المتكررة المتتالية (شائع في عناوين الصفحات)
        words = title.split()
        deduped = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
        title = ' '.join(deduped)
        title = re.sub(r'\s+', ' ', title).strip(' -|،,.')
        if not title:
            title = "Facebook Reel"

    if auto_shorts and duration and 0 < duration <= 60 \
            and not re.search(r"#shorts\b", f"{title} {suffix}", re.IGNORECASE):
        suffix = f"{suffix} #Shorts".strip()

    final = _compose_title_parts(prefix, title, suffix, YOUTUBE_TITLE_MAX)
    final = _drop_truncated_brand(final, raw_title)
    return _truncate_smart(final, YOUTUBE_TITLE_MAX) or "Facebook Reel"


def _clean_title_component(value):
    text = sanitize_public_text(value or "")
    return re.sub(r"\s+", " ", text).strip()


def _truncate_suffix_component(text, limit):
    """يُبقي #Shorts ظاهراً حتى عند قص قالب لاحقة طويل."""
    if not text or limit <= 0:
        return ""
    marker = "#Shorts"
    if re.search(r"#shorts\b", text, re.IGNORECASE):
        base = re.sub(r"\s*#shorts\b", " ", text, flags=re.IGNORECASE)
        base = re.sub(r"\s+", " ", base).strip()
        if limit <= len(marker):
            return marker[:limit]
        base = _truncate_smart(base, limit - len(marker) - 1)
        return f"{base} {marker}" if base else marker
    return _truncate_smart(text, limit)


def _compose_title_parts(prefix, title, suffix, limit=YOUTUBE_TITLE_MAX):
    """يرتب قالب العنوان مع حماية متن العنوان من ابتلاع البادئات الطويلة."""
    prefix = _clean_title_component(prefix)
    title = _clean_title_component(title) or "Facebook Reel"
    suffix = _clean_title_component(suffix)
    parts = [part for part in (prefix, title, suffix) if part]
    final = " ".join(parts)
    if len(final) <= limit:
        return final

    # نحجز نصف المساحة على الأقل للعنوان نفسه، ونوزع الباقي على القالب.
    body_budget = min(len(title), max(20, limit // 2))
    space_count = int(bool(prefix)) + int(bool(suffix))
    decoration_budget = max(0, limit - body_budget - space_count)
    if prefix and suffix:
        suffix_budget = min(len(suffix), decoration_budget // 2)
        prefix_budget = min(len(prefix), decoration_budget - suffix_budget)
        if prefix_budget + suffix_budget < decoration_budget:
            suffix_budget = min(len(suffix), decoration_budget - prefix_budget)
    elif suffix:
        suffix_budget = min(len(suffix), decoration_budget)
        prefix_budget = 0
    else:
        prefix_budget = min(len(prefix), decoration_budget)
        suffix_budget = 0

    prefix = _truncate_smart(prefix, prefix_budget)
    suffix = _truncate_suffix_component(suffix, suffix_budget)
    available = max(1, limit - len(prefix) - len(suffix)
                    - int(bool(prefix)) - int(bool(suffix)))
    if len(title) > available:
        if available > 1:
            clipped = _truncate_smart(title, available - 1)
            title = (clipped.rstrip(" .،,;:-—|·") + "…") if clipped else title[:available]
        else:
            title = title[:available]
    final = " ".join(part for part in (prefix, title, suffix) if part)
    if len(final) > limit:
        # Defensive final guard for pathological templates with no word boundaries.
        final = _truncate_smart(final, limit)
    return final


def normalize_youtube_title(title):
    """ينظف عنوان الرفع ويضمن ألا يكون فارغاً أو أطول من حد YouTube."""
    if title is None:
        return ""
    cleaned = _clean_title_component(title)
    return _truncate_smart(cleaned, YOUTUBE_TITLE_MAX) if cleaned else ""


def normalize_generated_title(title):
    """يرفض نواتج AI التي لا تحتوي إلا على وسوم أو علامات ترقيم."""
    cleaned = normalize_youtube_title(title)
    if not cleaned or not any(char.isalnum() for char in _remove_hashtags(cleaned)):
        return ""
    return cleaned

GENERIC_TITLE_VALUES = frozenset({
    "video", "videos", "facebook video", "facebook live", "live video",
    "facebook reel", "facebook reels", "reel", "reels", "reels video",
    "watch", "fb live", "مقطع فيديو", "فيديو", "بث مباشر", "فيديو مباشر",
})


def is_generic_title(title):
    """يكشف العناوين العامة التي يرجعها فيسبوك (og:title=Video مثلاً)؛
    هذه العناوين غير صالحة للنشر ولا يمكن الاعتماد عليها في مطابقة التكرار."""
    text = normalize_youtube_title(title)
    if not text or not any(char.isalnum() for char in text):
        return True
    return text.casefold().strip(" -|،.·#") in GENERIC_TITLE_VALUES


def _youtube_tag_cost(tag):
    return len(tag) + (2 if " " in tag else 0)


def normalize_youtube_tags(tags, log_cb=None, max_chars=YOUTUBE_TAGS_MAX):
    """ينظف الوسوم ويطبق حد YouTube الإجمالي، مع احتساب الفواصل والاقتباس للمسافات."""
    if isinstance(tags, str):
        candidates = [tags]
    else:
        try:
            candidates = list(tags or [])
        except TypeError:
            candidates = []
    normalized = []
    seen = set()
    used = 0
    dropped = 0
    for candidate in candidates:
        tag = unicodedata.normalize("NFC", str(candidate or "")).translate(BIDI_OVERRIDE_CHARS)
        tag = re.sub(r"[<>\x00-\x1f\x7f-\x9f]", " ", tag)
        tag = re.sub(r"\s+", " ", tag).strip()
        if not tag or tag.casefold() in seen:
            continue
        cost = _youtube_tag_cost(tag) + (1 if normalized else 0)
        if used + cost > max_chars:
            dropped += 1
            continue
        normalized.append(tag)
        seen.add(tag.casefold())
        used += cost
    if dropped and log_cb:
        log_cb(f"حُذفت {dropped} وسوم زائدة كي لا يتجاوز إجمالي الوسوم حد YouTube البالغ {max_chars} حرفاً.")
    return normalized

# ════════════════════════════════════════════════════
# DESCRIPTION BUILDER — وصف SEO مع هاشتاجات
# ════════════════════════════════════════════════════
AR_STOPWORDS = {'في', 'من', 'على', 'إلى', 'عن', 'أن', 'إن', 'ذا', 'ذلك', 'التي',
                'الذي', 'هذا', 'هذه', 'ما', 'لا', 'لم', 'لن', 'قد', 'و', 'أو',
                'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'on', 'for', 'is'}

def extract_hashtags(title, tags, max_tags=8):
    """يستخرج هاشتاجات من كلمات العنوان المميزة + الوسوم المضبوطة."""
    tags = [tags] if isinstance(tags, str) else (tags or [])
    tags = [str(t).strip().lstrip('#') for t in tags if str(t or '').strip()]
    found, seen = [], set()
    for w in _unicode_word_tokens(unicodedata.normalize("NFC", str(title or ""))):
        if len(w) < 3:
            continue
        wl = w.lower()
        if wl in AR_STOPWORDS or wl in seen:
            continue
        seen.add(wl)
        found.append(w)
        if len(found) >= max_tags:
            break
    merged, merged_seen = [], set()
    for t in tags + found:
        key = t.casefold()
        if key and key not in merged_seen:
            merged.append(t)
            merged_seen.add(key)
    return ' '.join(f'#{t.replace(" ", "_")}' for t in merged[:12])

def ensure_source_attribution(description, source_page="", source_url="", log_cb=None):
    body = (description or "").strip()
    attribution = []
    page_line = f"المصدر الأصلي: {source_page}" if source_page else ""
    url_line = f"رابط المقطع الأصلي: {source_url}" if source_url else ""
    if page_line and page_line not in body:
        attribution.append(page_line)
    if url_line and url_line not in body:
        attribution.append(url_line)
    attribution_text = "\n".join(attribution)
    if not attribution_text:
        if len(body) > YOUTUBE_DESCRIPTION_MAX:
            if log_cb:
                log_cb(f"قُصّر الوصف إلى {YOUTUBE_DESCRIPTION_MAX} حرفاً لحد YouTube؛ راجعه قبل النشر.")
            return body[:YOUTUBE_DESCRIPTION_MAX].rstrip()
        return body
    attribution_text = attribution_text[:YOUTUBE_DESCRIPTION_MAX].rstrip()
    separator = "\n\n" if body else ""
    body_limit = max(0, YOUTUBE_DESCRIPTION_MAX - len(separator) - len(attribution_text))
    if len(body) > body_limit and log_cb:
        log_cb(f"قُصّر متن الوصف لإبقاء المصدر ضمن حد YouTube البالغ {YOUTUBE_DESCRIPTION_MAX} حرفاً.")
    body = body[:body_limit].rstrip()
    return "\n\n".join(part for part in (body, attribution_text) if part)


def build_description(original_desc, extra_desc, title, tags,
                      auto_hashtags=True, credit_line="", log_cb=None):
    """يبني وصفاً نهائياً ضمن الحد الأقصى الذي يقبله YouTube."""
    parts = []
    if original_desc and original_desc.strip():
        parts.append(original_desc.strip())
    if extra_desc and extra_desc.strip():
        parts.append(extra_desc.strip())
    if auto_hashtags:
        tags_line = extract_hashtags(title, tags)
        if tags_line:
            parts.append(tags_line)
    if credit_line and credit_line.strip():
        parts.append(credit_line.strip())
    description = "\n\n".join(parts)
    if len(description) > YOUTUBE_DESCRIPTION_MAX:
        description = description[:YOUTUBE_DESCRIPTION_MAX].rstrip()
        if log_cb:
            log_cb(f"قُصّر الوصف إلى {YOUTUBE_DESCRIPTION_MAX} حرفاً لحد YouTube؛ راجعه قبل النشر.")
    return description
