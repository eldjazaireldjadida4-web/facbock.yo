from __future__ import annotations

import logging
import os
import time

from safe_logging import redact_sensitive_text


FACEBOOK_AUTH_COOKIE_NAMES = frozenset({"c_user", "xs"})
_FACEBOOK_HOME = "https://www.facebook.com/"


def _is_facebook_domain(domain: str) -> bool:
    host = str(domain or "").strip().lower().lstrip(".").rstrip(".")
    return host == "facebook.com" or host.endswith(".facebook.com")


def parse_netscape_cookies(path: str) -> list[dict]:
    cookies = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                raw_domain = line.split("\t", 1)[0]
                http_only = raw_domain.lower().startswith("#httponly_")
                if line.startswith("#") and not http_only:
                    continue
                fields = line.split("\t")
                if len(fields) < 7:
                    continue
                domain, include_subdomains, cookie_path, secure, expiry, name, value = fields[:7]
                http_only = domain.lower().startswith("#httponly_")
                if http_only:
                    domain = domain[len("#HttpOnly_"):]
                try:
                    expiry_value = int(expiry)
                except ValueError:
                    continue
                if not domain.strip() or not name.strip():
                    continue
                cookies.append({
                    "domain": domain.strip(),
                    "include_subdomains": include_subdomains.strip().upper() == "TRUE",
                    "path": cookie_path or "/",
                    "secure": secure.strip().upper() == "TRUE",
                    "expiry": expiry_value,
                    "name": name.strip(),
                    "value": value,
                    "http_only": http_only,
                })
    except OSError:
        return []
    return cookies


def _active_facebook_cookies(path: str, now: int | None = None) -> tuple[list[dict], dict]:
    current_time = int(time.time()) if now is None else int(now)
    # الحدّ الأدنى للتطبيع: مسار ``~/cookies.txt`` كان يُفحص موسّعاً في
    # facebook_cookie_setup_status ثم يُفتح خاماً هنا، فيظهر «جاهز» وقائمة
    # الكوكيز فارغة في الوقت نفسه.
    path = os.path.expanduser(str(path or "").strip())
    parsed = parse_netscape_cookies(path)
    facebook_cookies = [cookie for cookie in parsed if _is_facebook_domain(cookie["domain"])]
    active = [
        cookie for cookie in facebook_cookies
        if cookie["expiry"] == 0 or cookie["expiry"] > current_time
    ]
    active_names = {cookie["name"].lower() for cookie in active}
    found_auth_names = sorted(FACEBOOK_AUTH_COOKIE_NAMES & active_names)
    status = {
        "file_exists": os.path.isfile(path),
        "facebook_cookie_count": len(facebook_cookies),
        "active_facebook_cookie_count": len(active),
        "expired_facebook_cookie_count": len(facebook_cookies) - len(active),
        "session_cookies_present": found_auth_names,
        "has_active_session_pair": FACEBOOK_AUTH_COOKIE_NAMES <= active_names,
    }
    return active, status


def inspect_facebook_cookies(path: str, now: int | None = None) -> dict:
    _, status = _active_facebook_cookies(path, now=now)
    return status


def facebook_cookie_setup_status(enabled: bool, path: str, now: int | None = None) -> dict:
    cookie_path = os.path.expanduser(str(path or "").strip())
    base = {
        "status": "disabled",
        "active_cookie_count": 0,
        "expired_cookie_count": 0,
        "message": "استخدام كوكيز Facebook معطّل. فعّله بعد تسجيل الدخول وتصدير ملف كوكيز.",
    }
    if not enabled:
        return base
    if not cookie_path:
        return {**base, "status": "path_missing", "message": "حدّد ملف cookies.txt أولاً."}
    if not os.path.isfile(cookie_path):
        return {
            **base,
            "status": "file_missing",
            "message": "ملف cookies.txt غير موجود في المسار المحدد.",
        }

    inspected = inspect_facebook_cookies(cookie_path, now=now)
    result = {
        **base,
        "active_cookie_count": inspected["active_facebook_cookie_count"],
        "expired_cookie_count": inspected["expired_facebook_cookie_count"],
    }
    if not inspected["facebook_cookie_count"]:
        return {
            **result,
            "status": "invalid_file",
            "message": "الملف لا يحتوي على كوكيز Facebook بصيغة Netscape المتوقعة.",
        }
    if not inspected["active_facebook_cookie_count"]:
        return {
            **result,
            "status": "expired",
            "message": "كل كوكيز Facebook في الملف منتهية الصلاحية؛ سجّل الدخول وصدّر ملفاً جديداً.",
        }
    if not inspected["has_active_session_pair"]:
        return {
            **result,
            "status": "session_incomplete",
            "message": "لم يُعثر على زوج جلسة c_user/xs نشط؛ أعد تصدير الكوكيز بعد تسجيل الدخول.",
        }
    return {
        **result,
        "status": "ready",
        "message": (
            "الملف يحتوي على زوج جلسة نشط مبدئياً. لا يمكن تأكيد قبول Facebook للجلسة إلا "
            "باختبار اتصال فعلي؛ قد يطلب الموقع تحققاً يدوياً."
        ),
    }


def load_facebook_cookies(
    driver,
    cookies_path: str,
    log_cb=None,
    now: int | None = None,
    page_load_timeout: int = 20,
) -> dict:
    cookies, status = _active_facebook_cookies(cookies_path, now=now)
    report = {
        **status,
        "loaded_cookie_count": 0,
        "rejected_cookie_count": 0,
        "session_cookies_loaded": [],
        "navigation_failed": False,
    }
    if not cookies:
        if log_cb:
            if not status["file_exists"]:
                log_cb("ملف كوكيز Facebook غير موجود؛ لن تُستخدم جلسة مسجّلة.")
            elif not status["facebook_cookie_count"]:
                log_cb("ملف الكوكيز لا يحتوي على كوكيز لنطاق Facebook.")
            else:
                log_cb("لا توجد كوكيز Facebook غير منتهية الصلاحية في الملف المحدد.")
        return report

    try:
        page_load_timeout = max(5, min(int(page_load_timeout), 30))
    except (TypeError, ValueError):
        page_load_timeout = 20
    try:
        driver.set_page_load_timeout(page_load_timeout)
    except Exception:
        pass
    try:
        driver.get(_FACEBOOK_HOME)
    except Exception as error:
        # فيضان استثناء هنا كان يُسقط تحميل الكوكيز صامتاً في المستدعي
        # (except Exception: pass هناك)، فيُكشط الموقع زائراً غير مسجل.
        report["navigation_failed"] = True
        logging.warning(
            "تعذر فتح facebook.com لتحميل الكوكيز: %s",
            redact_sensitive_text(str(error))[:200],
        )
        if log_cb:
            log_cb("تعذر فتح facebook.com لتحميل كوكيز الجلسة؛ سيعمل الكشط دون تسجيل دخول.")
        return report
    time.sleep(1)
    current_host = "www.facebook.com"
    loaded_auth_names = set()
    host_navigations = 0
    max_host_navigations = 5
    for cookie_data in cookies:
        cookie_host = cookie_data["domain"].lstrip(".").lower()
        if not cookie_data["include_subdomains"] and cookie_host != current_host:
            # تصدير عادي يحمل كوكيز host-only لعشرات المضيفات؛ كل تنقل
            # صفحة كاملة قد يستهلك حتى مهلة التحميل. نحدّ التنقلات ونعدّ
            # ما تعذر إعداده بدل تعليق الكشط دقائق قبل كل دورة.
            if host_navigations >= max_host_navigations:
                report["rejected_cookie_count"] += 1
                continue
            try:
                driver.get(f"https://{cookie_host}/")
            except Exception as error:
                logging.warning(
                    "تعذر فتح %s لتحميل كوكيزه: %s",
                    cookie_host,
                    redact_sensitive_text(str(error))[:200],
                )
                report["rejected_cookie_count"] += 1
                continue
            host_navigations += 1
            current_host = cookie_host
        cookie = {
            "name": cookie_data["name"],
            "value": cookie_data["value"],
            "path": cookie_data["path"],
            "secure": cookie_data["secure"],
        }
        if cookie_data["include_subdomains"]:
            cookie["domain"] = cookie_data["domain"]
        if cookie_data["expiry"]:
            cookie["expiry"] = cookie_data["expiry"]
        if cookie_data["http_only"]:
            cookie["httpOnly"] = True
        try:
            driver.add_cookie(cookie)
        except Exception:
            report["rejected_cookie_count"] += 1
            continue
        report["loaded_cookie_count"] += 1
        if cookie_data["name"].lower() in FACEBOOK_AUTH_COOKIE_NAMES:
            loaded_auth_names.add(cookie_data["name"].lower())

    report["session_cookies_loaded"] = sorted(loaded_auth_names)
    if log_cb:
        log_cb(
            f"حُمّل {report['loaded_cookie_count']} من "
            f"{status['active_facebook_cookie_count']} كوكيز Facebook؛ "
            f"رُفض {report['rejected_cookie_count']} دون تسجيل قيمها."
        )
        if not status["has_active_session_pair"]:
            log_cb("الملف لا يحتوي على زوج كوكيز جلسة c_user/xs صالح مبدئياً؛ قد يلزم تصدير جديد.")
        elif FACEBOOK_AUTH_COOKIE_NAMES - loaded_auth_names:
            log_cb("تعذر تحميل أحد كوكيز جلسة Facebook؛ ستُرفض الجلسة إن أعاد الموقع التوجيه إلى تسجيل الدخول.")
    return report
