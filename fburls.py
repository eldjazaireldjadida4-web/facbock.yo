"""fburls.py — توحيد روابط صفحات ومقاطع فيسبوك وترتيب الريلز.

الاستيراد آمن: لا قراءة إعدادات ولا متصفح ولا نداءات شبكة عند import.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from facebook_scraper import canonicalize_facebook_video_url, facebook_media_id

FACEBOOK_HOSTS = ("facebook.com", "fb.com")


def normalize_channel_url(url):
    """يوحّد رابط صفحة Facebook إلى تبويب Reels مع رفض المخططات والاعتمادات غير الآمنة."""
    raw = (url or "").strip().strip('"').strip("'").strip()
    if not raw:
        raise ValueError("رابط الصفحة فارغ.")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlparse(raw)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError as error:
        raise ValueError("رابط صفحة Facebook غير صالح.") from error
    if (
        parts.scheme.lower() not in {"http", "https"}
        or parts.username is not None
        or parts.password is not None
        or (port is not None and port not in {80, 443})
        or not any(host == domain or host.endswith("." + domain) for domain in FACEBOOK_HOSTS)
    ):
        raise ValueError("رابط صفحة Facebook غير صالح.")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    segments = [segment for segment in path.split("/") if segment]
    lowered = {segment.lower() for segment in segments}
    sk_values = [value.lower() for key, value in query_items if key.lower() == "sk"]
    reels_tab_selected = any(value in {"reels_tab", "videos", "videos_tab"} for value in sk_values)
    about_selected = "about" in sk_values
    profile_id = next((value.strip() for key, value in query_items if key.lower() == "id"), "")
    if segments and segments[0].lower() == "profile.php":
        if not re.fullmatch(r"[0-9]{1,32}", profile_id):
            raise ValueError("رابط صفحة profile.php يحتاج معرّفاً رقمياً صالحاً.")
        path = "/profile.php"
        query = urlencode((("id", profile_id), ("sk", "reels_tab")))
        return urlunparse(("https", "www.facebook.com", path, "", query, ""))
    if lowered & {"reel", "reels", "videos"}:
        return urlunparse(("https", "www.facebook.com", path, "", "", ""))
    if reels_tab_selected:
        query = urlencode((("sk", "reels_tab"),))
        return urlunparse(("https", "www.facebook.com", path, "", query, ""))
    if len(segments) == 1:
        path = f"/{segments[0]}/reels"
    elif about_selected:
        query = urlencode((("sk", "reels_tab"),))
        return urlunparse(("https", "www.facebook.com", path, "", query, ""))
    return urlunparse(("https", "www.facebook.com", path, "", "", ""))


def _safe_normalize_channel_url(url):
    """يرفض الرابط غير الآمن بصمتٍ تشغيلياً من دون تمريره إلى المتصفح أو yt-dlp."""
    try:
        return normalize_channel_url(url)
    except ValueError:
        return ""


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
