"""fburls.py — توحيد روابط صفحات ومقاطع فيسبوك وترتيب الريلز.

الاستيراد آمن: لا قراءة إعدادات ولا متصفح ولا نداءات شبكة عند import.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlparse, urlunparse

from facebook_scraper import canonicalize_facebook_video_url, facebook_media_id

FACEBOOK_HOSTS = ("facebook.com", "fb.com")


def normalize_channel_url(url):
    """يوحّد رابط الصفحة ليشير إلى تبويب الريلز ويصلح الروابط الشائعة.
    facebook.com/name → /name/reels | profile.php?id=X → +sk=reels_tab
    """
    url = (url or "").strip().strip('"').strip("'").strip()
    if not url:
        raise ValueError("رابط الصفحة فارغ")
    if "://" not in url:
        url = "https://" + url
    parts = urlparse(url)
    host = parts.netloc.lower().split(":")[0]
    if not any(host == h or host.endswith("." + h) for h in FACEBOOK_HOSTS):
        raise ValueError(f"ليس رابط فيسبوك: {url}")
    path, query = parts.path, parts.query
    segments = [s for s in path.split("/") if s]
    lowered = {s.lower() for s in segments}
    if lowered & {"reel", "reels", "videos"} or "sk" in {k.lower() for k, _ in parse_qsl(query)}:
        return urlunparse(parts)
    if segments and segments[0].lower() == "profile.php":
        query = f"{query}&sk=reels_tab" if query else "sk=reels_tab"
    elif len(segments) == 1:
        path = f"/{segments[0]}/reels"
    return urlunparse((parts.scheme, parts.netloc, path, parts.params, query, parts.fragment))


def _safe_normalize_channel_url(url):
    """يوحّد الرابط، ويعيده كما هو إن لم يكن رابط فيسبوك صالحاً (لا يُفشل الدورة)."""
    try:
        return normalize_channel_url(url)
    except ValueError:
        return url


def reel_id_from_url_any(url):
    return facebook_media_id(url)


def order_reels(links, newest_first=True):
    """يوحّد روابط الفيديو ويزيل التكرار مع الحفاظ على ترتيب ثابت."""
    unique, seen = [], set()
    for link in links:
        canonical_url = canonicalize_facebook_video_url(str(link or ""))
        if not canonical_url:
            continue
        reel_id = reel_id_from_url_any(canonical_url) or canonical_url
        if reel_id not in seen:
            seen.add(reel_id)
            unique.append(canonical_url)
    if newest_first:
        def sort_key(url):
            reel_id = reel_id_from_url_any(url) or ""
            if reel_id.isdigit():
                return (0, -int(reel_id))
            return (1, reel_id.lower())
        unique.sort(key=sort_key)
    return unique
