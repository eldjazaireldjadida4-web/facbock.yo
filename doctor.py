"""doctor.py — فحص جاهزية بيئة FB → YouTube Uploader.

شغّله قبل البرنامج ليعطيك تقريراً واضحاً بما ينقص:

    py doctor.py        (Windows)
    python3 doctor.py   (Linux/macOS)

لا يحتاج مكتبات خارجية ولا يبدأ سحباً أو رفعاً. يجري فحص اتصال TCP قصيراً لفيسبوك أو للوكيل المضبوط، ويتحقق من وجود ملفات التفويض دون عرض محتوياتها.

الاستيراد كوحدة آمن: لا تنفيذ فحوصات ولا chdir ولا خروج عند import؛
المنطق داخل collect_checks() وmain() للاختبار الآلي.
"""

import json
import os
import shutil
import sys
import tempfile
import configparser

from dependency_check import missing_runtime_dependencies
from content_safety import (
    DEFAULT_OPERATOR_RIGHTS_STATEMENT,
    migrate_legacy_rights_gate_config,
    validate_automatic_rights_settings,
)
from facebook_auth import inspect_facebook_cookies
from facebook_scraper import DEFAULT_NETWORK_PREFLIGHT_TIMEOUT, facebook_network_route

APP_DIRECTORY = os.path.dirname(os.path.abspath(__file__))

OK, WARN, BAD, INFO = "[OK]", "[تحذير]", "[فشل]", "[معلومة]"


def _load_config(app_directory):
    """يقرأ config.ini من مجلد التطبيق ويطبق ترحيل بوابة الحقوق في الذاكرة."""
    config = configparser.ConfigParser(interpolation=None)
    error = None
    config_path = os.path.join(app_directory, "config.ini")
    try:
        # read() يبتلع OSError ويعيد قائمة فارغة، فيظهر «الملف موجود» مع أن
        # كل القيم المقروءة افتراضية. الفتح الصريح يجعل فشل الصلاحية مرئياً.
        with open(config_path, "r", encoding="utf-8") as handle:
            config.read_file(handle)
    except FileNotFoundError:
        pass
    except (configparser.Error, UnicodeError, OSError) as read_error:
        error = read_error
    if error is None:
        migrate_legacy_rights_gate_config(config)
    return config, error


def _collect_checks(add_check, config, config_error, app_directory):
    """ينفذ كل فحوصات الجاهزية بترتيبها الثابت ويضيف كل نتيجة عبر add_check."""

    def _config_value(section, key, fallback):
        return config.get(section, key, fallback=fallback)

    def _config_boolean(section, key, fallback):
        try:
            return config.getboolean(section, key, fallback=fallback)
        except ValueError:
            return None

    if config_error:
        add_check("ملف config.ini", BAD, f"تعذرت قراءته: {config_error}",
                  "أصلح صياغة INI أو ترميز UTF-8 قبل تشغيل التطبيق")

    # 1) إصدار Python
    major, minor = sys.version_info[:2]
    version_text = f"{major}.{minor}.{sys.version_info[2]}"
    if (major, minor) >= (3, 11):
        add_check("إصدار Python", OK, version_text)
    elif (major, minor) == (3, 10):
        add_check("إصدار Python", WARN, f"{version_text} — yt-dlp أسقط دعم 3.10 ومكتبات Google ستتبعه قريباً",
                  "ثبّت Python 3.12 من python.org")
    else:
        add_check("إصدار Python", BAD, f"{version_text} غير مدعوم",
                  "ثبّت Python 3.11 أو أحدث")

    # 2) المكتبات
    missing = missing_runtime_dependencies()
    if missing:
        add_check("مكتبات Python", BAD, "ناقصة أو غير قابلة للاستيراد: " + ", ".join(missing),
                  "شغّل setup.bat (Windows) أو ./setup.sh لتثبيت الحزم المتوافقة")
    else:
        add_check("مكتبات Python", OK, "كل المكتبات المطلوبة موجودة، بما فيها portalocker")

    # 3) Tkinter (الواجهة)
    try:
        import tkinter  # noqa: F401
        add_check("واجهة Tkinter", OK, "مثبتة")
    except ImportError:
        add_check("واجهة Tkinter", BAD, "غير مثبتة",
                  "Windows: أعد تثبيت Python مع خيار tcl/tk — Linux: sudo apt install python3-tk")

    # 4) متصفح Chrome/Chromium
    browsers = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]
    found_browser = next((shutil.which(b) for b in browsers if shutil.which(b)), None)
    if not found_browser:
        for candidate in [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]:
            if os.path.exists(candidate):
                found_browser = candidate
                break
    if found_browser:
        add_check("متصفح Chrome", OK, os.path.basename(found_browser))
    else:
        add_check("متصفح Chrome", BAD, "غير موجود",
                  "ثبّت Google Chrome (Selenium يجلب chromedriver تلقائياً بعدها)")

    # 5) ffmpeg / ffprobe
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        add_check("ffmpeg", OK, ffmpeg_path)
    else:
        add_check("ffmpeg", WARN, "غير موجود",
                  "اختياري للتنزيل بصيغة مدمجة؛ ثبّته من ffmpeg.org لميزات الشعار والمعالجة.")
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        add_check("ffprobe", OK, ffprobe_path)
    else:
        add_check("ffprobe", BAD, "غير موجود؛ لا يمكن التحقق من الفيديو",
                  "ثبّت حزمة FFmpeg التي تتضمن ffprobe وأضفها إلى PATH؛ لن يُرفع ملف دون فحص.")

    # 6) client_secrets.json
    configured_secrets_file = _config_value("SETTINGS", "CLIENT_SECRETS_FILE", "client_secrets.json")
    secrets_file = configured_secrets_file
    if not os.path.isabs(secrets_file):
        secrets_file = os.path.join(app_directory, secrets_file)
    if not os.path.exists(secrets_file):
        add_check("ملف تفويض Google", BAD, f"{configured_secrets_file} مفقود",
                  "أنشئ OAuth client نوع Desktop في Google Cloud ونزّل الملف بجانب البرنامج")
    else:
        try:
            with open(secrets_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # ملف فارغ أو {"installed": null} هو الحالة الشائعة لتنزيل ناقص؛
            # كان يُسقط doctor بترايس باك قبل أن يطبع أي حكم.
            kind = None
            if isinstance(data, dict):
                kind = "installed" if "installed" in data else ("web" if "web" in data else None)
            section = data.get(kind) if isinstance(data, dict) and kind else None
            if isinstance(section, dict) and section.get("client_id") and section.get("client_secret"):
                note = "نوع Desktop (مناسب)" if kind == "installed" else "نوع Web — قد لا يناسب الربط المحلي"
                add_check("ملف تفويض Google", OK if kind == "installed" else WARN, note)
            else:
                add_check("ملف تفويض Google", BAD, "البنية غير مكتملة", "أعد تنزيل ملف OAuth")
        except (ValueError, OSError, TypeError) as error:
            add_check("ملف تفويض Google", BAD, f"غير صالح: {error}", "أعد تنزيل ملف OAuth")

    # 7) جلسة Facebook
    configured_cookies_file = _config_value(
        "STUDIO", "COOKIES_FILE", _config_value("SETTINGS", "COOKIES_FILE", "cookies.txt")
    )
    cookies_file = configured_cookies_file
    if not os.path.isabs(cookies_file):
        cookies_file = os.path.join(app_directory, cookies_file)
    cookies_enabled = _config_boolean("STUDIO", "COOKIES_ENABLED", False)
    if cookies_enabled is None:
        add_check("كوكيز Facebook", BAD, "قيمة COOKIES_ENABLED غير صالحة؛ استخدم true أو false")
    elif not cookies_enabled:
        add_check("كوكيز Facebook", WARN, "استخدام الكوكيز معطّل في الإعدادات",
                  "فعّله من إعدادات السحب إذا كانت الصفحات تتطلب تسجيل الدخول")
    elif not os.path.isfile(cookies_file):
        add_check("كوكيز Facebook", WARN, f"الملف المحدد غير موجود: {os.path.basename(cookies_file)}",
                  "صدّر ملفاً جديداً من جلسة Facebook مسجّلة، ثم حدّده في الإعدادات")
    else:
        cookie_status = inspect_facebook_cookies(cookies_file)
        if not cookie_status["active_facebook_cookie_count"]:
            add_check("كوكيز Facebook", WARN, "لم يُعثر على كوكيز Facebook صالحة زمنياً في الملف",
                      "صدّر ملفاً جديداً من جلسة Facebook مسجّلة؛ لا تشارك الملف")
        elif not cookie_status["has_active_session_pair"]:
            add_check("كوكيز Facebook", WARN, "لم يُعثر على زوج جلسة c_user/xs نشط",
                      "أعد تصدير الكوكيز بعد تسجيل الدخول؛ لا تُعرض قيمها في هذا التقرير")
        else:
            add_check("كوكيز Facebook", OK, "عُثر على c_user/xs نشطين مبدئياً؛ قد يطلب Facebook تحققاً يدوياً رغم ذلك")

    # 7.1) بوابة مراجعة سياسة المحتوى (اختيارية)
    safety_enabled = _config_boolean("SAFETY", "ENABLED", False)
    if safety_enabled is None:
        add_check("بوابة مراجعة المحتوى", BAD, "قيمة SAFETY.ENABLED غير صالحة؛ استخدم true أو false")
    elif safety_enabled:
        cutter_root = _config_value("SAFETY", "CUTTER_ROOT", "").strip()
        cutter_python = _config_value("SAFETY", "CUTTER_PYTHON", "").strip()
        review_errors = []
        if not cutter_root:
            review_errors.append("حدد مجلد OUSSAMA Cutter في الإعدادات.")
        else:
            review_script = os.path.join(cutter_root, "scripts", "upload_content_review.py")
            if not os.path.isdir(cutter_root):
                review_errors.append("مجلد OUSSAMA Cutter غير موجود.")
            elif not os.path.isfile(review_script):
                review_errors.append("scripts/upload_content_review.py غير موجود داخل Cutter.")
        if cutter_python and not os.path.isfile(cutter_python):
            review_errors.append("مفسّر Python المحدد غير موجود.")
        if review_errors:
            add_check("بوابة مراجعة المحتوى", BAD, "إعداد غير مكتمل: " + "؛ ".join(review_errors),
                      "أصلح المسارات في تبويب الاستوديو أو عطّل البوابة عمداً")
        else:
            add_check("بوابة مراجعة المحتوى", OK, "مهيّأة؛ الرفع يتوقف عند غياب تقرير نصّي صالح")
    else:
        add_check("بوابة مراجعة المحتوى", WARN, "معطّلة؛ لا تُشغّل مراجعة نصية إضافية",
                  "فعّلها من تبويب الاستوديو فقط بعد مراجعة إعدادات مزوّد الذكاء الاصطناعي")

    allow_public_automation = _config_boolean("SAFETY", "ALLOW_PUBLIC_AUTOMATION", False)
    if allow_public_automation is None:
        add_check("السماح بالنشر الآلي العام", BAD, "قيمة غير صالحة؛ استخدم true أو false")
    elif allow_public_automation:
        add_check("السماح بالنشر الآلي العام", WARN, "مفعّل؛ قد تُنشر الفيديوهات العامة أو المجدولة دون موافقة لكل فيديو",
                  "عطّله ما لم تكن قد راجعت سياسة النشر الآلي")
    else:
        add_check("السماح بالنشر الآلي العام", OK, "مغلق؛ يلزم تأكيد يدوي قبل النشر العام أو المجدول")

    # 7.5) أذونات الملفات السرية (لينكس/ماك فقط)
    if os.name == "posix":
        import glob as _glob
        _secret_paths = [secrets_file, cookies_file, "config.ini", "ai_keys.json"]
        _secret_paths += _glob.glob("*_token.json")
        for _p in _secret_paths:
            if os.path.isfile(_p):
                _mode = os.stat(_p).st_mode & 0o777
                if _mode & 0o077:
                    add_check(f"أذونات {_p}", WARN, f"{_mode:o} — يمكن لمستخدمين آخرين قراءتها",
                              "نفّذ: chmod 600 " + _p)

    # 8) ربط قناة YouTube (ملف التوكن)
    tokens = sorted(n for n in os.listdir(app_directory) if n.endswith("_token.json"))
    if tokens:
        add_check("ربط قناة YouTube", OK, "تم العثور على: " + ", ".join(tokens))
    else:
        add_check("ربط قناة YouTube", INFO, "لا يوجد ملف توكن — سيُطلب الربط عند أول تشغيل (ضغطة Allow)")

    # 8.5) بوابة الحقوق (وضع الإقرار المحفوظ)
    if os.path.exists(os.path.join(app_directory, "config.ini")):
        _gate_mode = str(_config_value("STUDIO", "RIGHTS_GATE_MODE", "strict")).strip().lower()
        _gate_settings = {
            "rights_gate_mode": _gate_mode,
            "rights_operator_basis": _config_value("STUDIO", "RIGHTS_OPERATOR_BASIS", "owned"),
            "rights_operator_statement": _config_value(
                "STUDIO", "RIGHTS_OPERATOR_STATEMENT", DEFAULT_OPERATOR_RIGHTS_STATEMENT
            ),
            "rights_operator_evidence": _config_value("STUDIO", "RIGHTS_OPERATOR_EVIDENCE", ""),
            "review_before_upload": _config_boolean("STUDIO", "REVIEW_BEFORE_UPLOAD", True),
            "dry_run": _config_boolean("SETTINGS", "DRY_RUN", False),
        }
        _gate_problems = validate_automatic_rights_settings(_gate_settings)
        if _gate_problems:
            add_check("بوابة الحقوق", BAD, " — ".join(_gate_problems),
                      "أكمل إقرار الحقوق الصادق، أو فعّل المراجعة اليدوية لكل فيديو؛ لا تبدأ دورة ستتوقف بعد التنزيل.")
        elif _gate_mode == "operator_confirmed":
            add_check("بوابة الحقوق", OK, "operator_confirmed — يُرفع آلياً بناءً على إقرار المشغل المحفوظ؛ المسؤولية على المشغل")
        elif _gate_mode == "strict":
            add_check("بوابة الحقوق", OK, "strict — المراجعة اليدوية لكل فيديو أو وضع المحاكاة مفعّل")
        else:
            add_check("بوابة الحقوق", BAD, f"قيمة غير معروفة: {_gate_mode}", "استخدم strict أو operator_confirmed")

    # 9) config.ini
    if os.path.exists(os.path.join(app_directory, "config.ini")):
        if not config_error:
            add_check("ملف config.ini", OK, "موجود")
        else:
            add_check("ملف config.ini", BAD, f"موجود لكن تعذّرت قراءته: {config_error}",
                      "افحص صلاحيات الملف وترميزه؛ القيم المقيمة أدناه افتراضية وليست من ملفك")
    else:
        add_check("ملف config.ini", INFO, "غير موجود — ستُستخدم القيم الافتراضية",
                  "انسخ config.ini.example إلى config.ini إن أردت تخصيص المسارات")

    # 10) صلاحية الكتابة والمساحة
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".doctor_write_test.",
            dir=app_directory, delete=True,
        ) as f:
            f.write("ok")
        free_gb = round(shutil.disk_usage(app_directory).free / (1024 ** 3), 1)
        add_check("الكتابة على القرص", OK, f"المساحة الحرة {free_gb} GB")
    except OSError as error:
        add_check("الكتابة على القرص", BAD, str(error), "شغّل البرنامج من مجلد تملك صلاحية الكتابة عليه")

    proxy_raw = (_config_value("WATCH", "PROXY", "") or "").strip()
    force_ipv4 = _config_boolean("WATCH", "FORCE_IPV4", True)
    if force_ipv4 is None:
        add_check("تفضيل IPv4", WARN, "قيمة FORCE_IPV4 غير صالحة؛ استُخدم true",
                  "استخدم true أو false بصيغة يقبلها configparser")
        force_ipv4 = True
    try:
        network_timeout = max(
            10,
            min(config.getint("WATCH", "NETWORK_PREFLIGHT_TIMEOUT", fallback=DEFAULT_NETWORK_PREFLIGHT_TIMEOUT), 15),
        )
    except (ValueError, configparser.Error):
        network_timeout = DEFAULT_NETWORK_PREFLIGHT_TIMEOUT
    route = facebook_network_route(proxy_raw, force_ipv4, timeout=network_timeout)

    if proxy_raw:
        if route == "proxy":
            add_check("الوكيل المضبوط", OK, "يستجيب؛ سيُستخدم في Chrome وyt-dlp")
        else:
            add_check("الوكيل المضبوط", WARN, "تعذر الاتصال بالوكيل خلال المهلة المضبوطة", "تحقق من عنوان الوكيل ومن تشغيل خدمته؛ لن يُعرض اسم المستخدم أو كلمة المرور.")
    elif route == "ipv4":
        add_check("الاتصال بفيسبوك", OK, "المنفذ 443 يستجيب عبر IPv4")
    elif route == "ipv6":
        detail = "المنفذ 443 يستجيب عبر IPv6؛ سيتحول التطبيق إليه تلقائياً لأن IPv4 غير متاح." if force_ipv4 else "المنفذ 443 يستجيب عبر IPv6."
        add_check("الاتصال بفيسبوك", OK, detail)
    elif route:
        add_check("الاتصال بفيسبوك", OK, "المنفذ 443 يستجيب عبر المسار المباشر")
    else:
        add_check("الاتصال بفيسبوك", WARN, "تعذر الوصول إلى Facebook عبر IPv4 وIPv6 ضمن المهلة المضبوطة", "تحقق من الإنترنت أو من حجب مزود الخدمة؛ استخدم وكيلاً صالحاً في [WATCH] إذا كان متاحاً.")

    if not proxy_raw:
        add_check("الوكيل المضبوط", INFO, "غير مضبوط — إذا كان مزودك يحجب فيسبوك مباشرة عيّن PROXY في [WATCH]", "أمثلة: socks5://127.0.0.1:1080 أو http://127.0.0.1:8080")


def main():
    """نقطة التشغيل: نفس سلوك السكربت السابق حرفياً مع رمز خروج صحيح."""
    os.chdir(APP_DIRECTORY)
    config, config_error = _load_config(APP_DIRECTORY)
    results = []

    def add_check(name, status, detail="", fix=""):
        results.append((status, name, detail, fix))
        line = f"{status} {name}"
        if detail:
            line += f" — {detail}"
        print(line)
        if fix and status in (BAD, WARN):
            print(f"     ↳ {fix}")

    print("=" * 62)
    print("  فحص جاهزية FB → YouTube Uploader")
    print("=" * 62)

    _collect_checks(add_check, config, config_error, APP_DIRECTORY)

    print("=" * 62)
    bad = sum(1 for s, *_ in results if s == BAD)
    warn = sum(1 for s, *_ in results if s == WARN)
    if bad:
        print(f"النتيجة: [فشل] {bad} مشكلة تمنع التشغيل، و{warn} تحذير — راجع البنود أعلاه.")
    elif warn:
        print(f"النتيجة:  لا مانع من التشغيل، لكن هناك {warn} تحذير يُفضّل معالجته.")
    else:
        print("النتيجة: [OK] البيئة جاهزة للتشغيل.")
    print("=" * 62)

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
