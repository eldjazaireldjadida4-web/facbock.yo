from __future__ import annotations

from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from selenium.common.exceptions import TimeoutException, WebDriverException

from facebook_auth import facebook_cookie_setup_status
from facebook_scraper import facebook_auth_block_reason, is_transient_facebook_error


_FACEBOOK_DOMAINS = ("facebook.com", "fb.com")
_AUTH_MESSAGES = {
    "login": (
        "Facebook أعاد صفحة المصدر إلى تسجيل الدخول. فعّل ملف cookies.txt بعد تسجيل الدخول "
        "وصدّره من جديد إذا استمر الرفض."
    ),
    "checkpoint": (
        "Facebook يطلب تحققاً أمنياً للحساب. أكمله يدوياً ثم صدّر cookies.txt جديدة؛ "
        "الاختبار لا يتجاوز فحوص الأمان."
    ),
    "recovery": (
        "Facebook يطلب استرداد الحساب. أكمل الخطوة يدوياً ثم حدّث ملف cookies.txt."
    ),
}


def safe_facebook_page_url(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if parts.username is not None or parts.password is not None or port not in {None, 80, 443}:
        return None
    if not any(host == domain or host.endswith(f".{domain}") for domain in _FACEBOOK_DOMAINS):
        return None
    return urlunsplit(("https", "www.facebook.com", parts.path or "/", parts.query, ""))


def _result(status: str, message: str, network_route: str | None = None) -> dict:
    return {"status": status, "message": message, "network_route": network_route}


def probe_facebook_page_access(
    page_url: str,
    cookies_enabled: bool,
    cookies_file: str,
    *,
    network_probe: Callable[[], str | None],
    driver_factory: Callable[[], object | None],
    cookie_loader: Callable[[object, str], dict],
    auth_reason: Callable[[str, str], str | None] = facebook_auth_block_reason,
    transient_error: Callable[[BaseException], bool] = is_transient_facebook_error,
    page_timeout: int = 20,
) -> dict:
    target_url = safe_facebook_page_url(page_url)
    if not target_url:
        return _result("invalid_url", "اختر رابط صفحة صالحاً من Facebook قبل إجراء الاختبار.")

    if cookies_enabled:
        cookie_status = facebook_cookie_setup_status(True, cookies_file)
        if cookie_status["status"] != "ready":
            return _result("cookies_unready", cookie_status["message"])

    try:
        network_route = network_probe()
    except Exception:
        return _result("network_unavailable", "تعذر فحص اتصال Facebook أو الوكيل.")
    if not network_route:
        return _result(
            "network_unavailable",
            "تعذر الوصول إلى Facebook عبر الشبكة أو الوكيل. افحص الإنترنت والوكيل ثم أعد الاختبار.",
        )

    try:
        driver = driver_factory()
    except Exception:
        driver = None
    if driver is None:
        return _result("browser_unavailable", "تعذر تشغيل Chrome للاختبار. راجع تثبيت Chrome وإعداد المتصفح.", network_route)

    try:
        try:
            driver.set_page_load_timeout(max(5, min(int(page_timeout), 30)))
        except Exception:
            pass

        if cookies_enabled:
            try:
                cookie_report = cookie_loader(driver, cookies_file)
            except Exception:
                return _result(
                    "cookies_not_loaded",
                    "تعذر تحميل ملف الجلسة في متصفح الاختبار. أعد تصدير cookies.txt وتحقق من المسار.",
                    network_route,
                )
            loaded_names = {
                str(name).strip().lower()
                for name in (cookie_report or {}).get("session_cookies_loaded", [])
            }
            if not {"c_user", "xs"}.issubset(loaded_names):
                return _result(
                    "cookies_not_loaded",
                    "لم يتمكن Chrome من تحميل كوكيزي الجلسة c_user وxs معاً. أعد تصدير cookies.txt.",
                    network_route,
                )

        timed_out = False
        try:
            driver.get(target_url)
        except TimeoutException:
            timed_out = True
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
        except WebDriverException as error:
            if transient_error(error):
                return _result(
                    "network_unavailable",
                    "انقطع الاتصال أثناء فتح صفحة Facebook. افحص الشبكة أو الوكيل ثم أعد الاختبار.",
                    network_route,
                )
            return _result(
                "browser_error",
                "تعذر على Chrome فتح صفحة Facebook. أعد تشغيل البرنامج وافحص Chrome.",
                network_route,
            )
        except Exception as error:
            status = "network_unavailable" if transient_error(error) else "browser_error"
            message = (
                "انقطع الاتصال أثناء فتح صفحة Facebook. افحص الشبكة أو الوكيل ثم أعد الاختبار."
                if status == "network_unavailable"
                else "تعذر على Chrome فتح صفحة Facebook. أعد تشغيل البرنامج وافحص Chrome."
            )
            return _result(status, message, network_route)

        try:
            current_url = str(getattr(driver, "current_url", "") or "")
            page_source = str(getattr(driver, "page_source", "") or "")
        except Exception:
            return _result("browser_error", "تعذر قراءة نتيجة الاختبار من Chrome.", network_route)

        auth = auth_reason(current_url, page_source)
        if auth:
            status = {
                "login": "login_required",
                "checkpoint": "security_checkpoint",
                "recovery": "recovery_required",
            }.get(auth, "login_required")
            return _result(status, _AUTH_MESSAGES.get(auth, _AUTH_MESSAGES["login"]), network_route)
        if timed_out:
            return _result(
                "page_timeout",
                "انتهت مهلة تحميل الصفحة؛ لم يظهر تحويل واضح لتسجيل الدخول، لكن الوصول لم يتأكد. أعد الاختبار أو افحص الشبكة.",
                network_route,
            )
        return _result(
            "page_accessible",
            "لم يظهر تحويل إلى تسجيل الدخول أو التحقق. هذا لا يضمن إمكانية استخراج المقاطع أو نجاح الرفع.",
            network_route,
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass
