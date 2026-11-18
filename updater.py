"""updater.py — التحقق من التحديثات من GitHub Releases وتنزيل المثبّت.

الاستيراد آمن: لا قراءة إعدادات ولا نداءات شبكة عند import. كل النداءات
تستخدم urllib (المكتبة القياسية) بمهلة قصيرة، وأي فشل شبكي أو تحليلي
يُعيد None بهدوء — فحص التحديثات لا يجوز أن يُسقط التطبيق أو يعلّقه
أبداً. التنزيل والتثبيت لا يحدثان إلا بنقرة صريحة من المستخدم في
نافذة التحديث؛ الفحص التلقائي عند البدء لا ينزّل شيئاً.

قناة التحديث: أحدث إصدار غير تجريبي في المستودع العام عبر
``/releases/latest`` (بلا مصادقة)، والمثبّت هو أصل الإصدار الذي ينتهي
بـ ``-windows-x86_64-setup.exe``. التحقق من التنزيل: الحجم المعلن في
الإصدار + بصمة PE (``MZ``) في أول بايتين.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = "eldjazaireldjadida4-web/facbock.yo"
RELEASES_LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALLER_ASSET_SUFFIX = "-windows-x86_64-setup.exe"
CHECK_INTERVAL_HOURS = 24
REQUEST_TIMEOUT = 10
DOWNLOAD_TIMEOUT = 120
_CHUNK_SIZE = 1024 * 1024


class UpdateError(Exception):
    """فشل تنزيل المثبّت أو التحقق منه (حجم أو بصمة غير مطابقة)."""


@dataclass(frozen=True)
class UpdateInfo:
    """إصدار أحدث متوفر على GitHub مع رابط مثبّت Windows الخاص به."""

    version: str
    tag: str
    html_url: str
    download_url: str
    size: int
    body: str = ""


def is_frozen() -> bool:
    """صحيح عندما يعمل التطبيق من حزمة PyInstaller (لا من المصدر)."""
    return bool(getattr(sys, "frozen", False))


def parse_version(text) -> tuple[int, ...] | None:
    """``v4.49.30`` أو ``4.49.30`` → ``(4, 49, 30)``؛ أي صيغة أخرى → None."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip().lstrip("vV").strip()
    if not cleaned:
        return None
    parts = cleaned.split(".")
    numbers = []
    for part in parts:
        if not part.isdigit():
            return None
        numbers.append(int(part))
    return tuple(numbers) if numbers else None


def is_newer(latest: tuple[int, ...], current: tuple[int, ...]) -> bool:
    """مقارنة رقمية مع تمديد الأقصر بالأصفار: ``(4, 50)`` أحدث من ``(4, 49, 29)``."""
    width = max(len(latest), len(current))
    padded_latest = latest + (0,) * (width - len(latest))
    padded_current = current + (0,) * (width - len(current))
    return padded_latest > padded_current


def _pick_installer_asset(release: dict, latest_version: str) -> dict | None:
    """أصل المثبّت في الإصدار؛ يُفضَّل المطابق لرقم الإصدار المعلن."""
    assets = release.get("assets") or []
    candidates = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and asset["name"].endswith(INSTALLER_ASSET_SUFFIX)
    ]
    if not candidates:
        return None
    for asset in candidates:
        if latest_version in asset["name"]:
            return asset
    return candidates[0]


def check_for_update(current_version, opener=None, timeout=REQUEST_TIMEOUT, on_error=None):
    """يعيد UpdateInfo عند وجود إصدار أحدث، وNone عند الحداثة أو أي فشل.

    لا يرفع أبداً: انقطاع الشبكة أو استجابة GitHub غير المتوقعة تعني
    ببساطة «لا تحديث معروف الآن»، ويُسجَّل نوع الخطأ فقط في السجل.
    ``opener`` قابل للحقن في الاختبارات (يستقبل Request وtimeout)،
    و``on_error`` يُستدعى بالاستثناء عند الفشل ليميز المتصل بين
    «لا تحديث» و«تعذر الوصول».
    """
    current = parse_version(current_version)
    if current is None:
        logging.warning("رقم الإصدار الحالي غير صالح (%r)؛ تخطيت فحص التحديثات.", current_version)
        return None
    fetch = opener or urllib.request.urlopen
    try:
        request = urllib.request.Request(
            RELEASES_LATEST_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"facbock-yo/{current_version}",
            },
        )
        with fetch(request, timeout=timeout) as response:
            payload = response.read()
        release = json.loads(payload.decode("utf-8"))
        if not isinstance(release, dict):
            return None
        latest = parse_version(release.get("tag_name", ""))
        if latest is None or not is_newer(latest, current):
            return None
        asset = _pick_installer_asset(release, release["tag_name"].lstrip("vV"))
        if asset is None or not asset.get("browser_download_url"):
            logging.warning("الإصدار %s بلا أصل مثبّت Windows؛ تخطيت فحص التحديثات.", release.get("tag_name"))
            return None
        return UpdateInfo(
            version=".".join(str(n) for n in latest),
            tag=str(release["tag_name"]),
            html_url=str(release.get("html_url") or ""),
            download_url=str(asset["browser_download_url"]),
            size=int(asset.get("size") or 0),
            body=str(release.get("body") or ""),
        )
    except Exception as error:  # noqa: BLE001 — الفحص لا يجوز أن يرفع أبداً
        logging.warning("تعذر فحص التحديثات (%s).", type(error).__name__)
        if on_error is not None:
            on_error(error)
        return None


def download_installer(info, dest_dir=None, opener=None, progress_cb=None, timeout=DOWNLOAD_TIMEOUT):
    """ينزّل مثبّت Windows ويتحقق من حجمه وبصمة PE؛ يعيد مسار الملف.

    يرفع UpdateError عند أي عدم تطابق أو فشل شبكي، ويحذف أي ملف
    جزئي. ``progress_cb`` اختياري ويُستدعى بـ (المنزَّل، الإجمالي).
    """
    if not info.download_url or not info.download_url.startswith("https://"):
        raise UpdateError("رابط تنزيل المثبّت غير صالح.")
    filename = info.download_url.rsplit("/", 1)[-1]
    if not filename.endswith(INSTALLER_ASSET_SUFFIX) or "/" in filename or "\\" in filename:
        raise UpdateError("اسم ملف المثبّت غير متوقع.")
    directory = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    fetch = opener or urllib.request.urlopen
    downloaded = 0
    try:
        request = urllib.request.Request(
            info.download_url, headers={"User-Agent": f"facbock-yo/{info.version}"}
        )
        with fetch(request, timeout=timeout) as response, target.open("wb") as handle:
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress_cb is not None:
                    progress_cb(downloaded, info.size)
    except UpdateError:
        raise
    except Exception as error:  # noqa: BLE001 — أي فشل شبكي يتحول إلى UpdateError
        _remove_quietly(target)
        raise UpdateError(f"تعذر تنزيل المثبّت ({type(error).__name__}).") from error
    if info.size and downloaded != info.size:
        _remove_quietly(target)
        raise UpdateError(f"حجم المثبّت المنزَّل ({downloaded}) لا يطابق المعلن ({info.size}).")
    with target.open("rb") as handle:
        magic = handle.read(2)
    if magic != b"MZ":
        _remove_quietly(target)
        raise UpdateError("الملف المنزَّل ليس مثبّت Windows صالحاً (بصمة PE مفقودة).")
    return target


def _remove_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def launch_installer(path):
    """يشغّل المثبّت المنزَّل ويعيد كائن العملية؛ على المتصل إغلاق التطبيق بعدها."""
    return subprocess.Popen([str(path)])


def should_check_now(last_check, now=None, interval_hours=CHECK_INTERVAL_HOURS) -> bool:
    """صحيح عند غياب آخر فحص أو مرور الفاصل؛ طابع مستقبلي أو تالف يعني «الآن»."""
    if not last_check:
        return True
    try:
        previous = datetime.fromisoformat(str(last_check))
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current < previous:
        return True
    return current - previous >= timedelta(hours=interval_hours)
