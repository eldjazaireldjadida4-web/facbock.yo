# AGENTS.md — facbock.yo

أداة سطح مكتب (Python/Tkinter) تسحب فيديوهات Reels من صفحات Facebook، تُحسّن
العنوان والوصف والصورة المصغّرة بالذكاء الاصطناعي، تضع الشعار عبر FFmpeg، ثم
ترفعها إلى YouTube مع بوابة حقوق صارمة وإيصالات رفع قابلة للتدقيق.

## خريطة الوحدات

| الملف | الدور |
|---|---|
| `fb_youtube_uploader_v49_pro.py` | نقطة الدخول: الواجهة الرسومية (صنف `App`)، الإعدادات، الحالة (`load_state`/`save_state`/`STATE_FILE`)، `QuotaManager`، منع التكرار (`content_fingerprint`...)، وضع CLI |
| `upload_pipeline.py` | أنبوب الرفع: فلاتر المدة والفئات، `validate_media`، `upload_video`، `verify_upload`، `finalize_video`، `next_publish_time`، ثوابت إعادة المحاولة (`MAX_RETRIES`، `RETRY_DELAY`...) |
| `media_processing.py` | فحص الفيديو (`probe_video`)، تركيب الشعار (`apply_logo_overlay`)، الصور المصغّرة (`generate_thumbnail`، `upload_thumbnail`...)، قصّ Shorts |
| `doctor.py` | فحص ما قبل الإقلاع (المتصفح، FFmpeg، الشبكة، ملفات التفويض) |
| `diagnostic_report.py` | توليد تقرير تشخيصي مصنّف + `probe_service` لفحص TCP/DNS |
| `texttools.py` | تطبيع العناوين والوسوم والوصف وحدود YouTube |
| `ai_client.py` | عميل Gemini وتدوير المفاتيح وتنقيح المخرجات |
| `content_review_bridge.py` | بوابة مراجعة المحتوى وربطها بقرارات المشغّل |
| `control_plane.py` | فلاتر النشر (`validate_publication_controls`)، استخراج معرف الفيديو، إدارة الإيقاف، دورة حياة الإيصالات (`begin_upload_attempt`، `mark_upload_result_unknown`، `reconcile_upload_result`) — إيصالات الرفع (`load_upload_receipts`/`compact_upload_receipts`/`save_upload_receipts` و`UPLOAD_RECEIPTS_FILE`) تبقى في الملف الرئيسي لأن اختباراتها تخترق ثوابت المسارات على `app` |
| `facebook_scraper.py` | جلب روابط Reels ومسار الشبكة إلى فيسبوك |
| `fburls.py` | تطبيع روابط صفحات وقنوات فيسبوك |
| `uploaderrors.py` | تصنيف أخطاء الرفع (`classify_upload_error`، `_http_status`...) |
| `file_security.py` | `restrict_file_permissions`، `open_private_append` |
| `safe_logging.py` | سجلات دوّارة مع تنقيح الأسرار (`redact_sensitive_text`) |
| `version.py` | المصدر الوحيد لرقم الإصدار (`APP_VERSION`)؛ يستورده الملف الرئيسي و`diagnostic_report` |
| `state_repair/ledger` | إصلاح ملفات الحالة التالفة (`repair_state_files`) يبقى في الملف الرئيسي لأن اختباراته تخترق ثوابت المسارات على `app` |
| `local_transcription.py` | تفريغ صوتي محلي اختياري |
| `notify.py` | إشعارات Telegram الاختيارية |
| `ai_transcription_cache.py` | ذاكرة التفريغ الصوتي |
| `settings_validation.py` | التحقق من صحة الإعدادات |
| `pipeline_errors.py` | استثناء `PipelineFailure` للمسار |

## قواعد إعادة الهيكلة (مُجرَّبة)

- الاختبارات تُحمّل الملف الرئيسي عبر `importlib` بأسماء مخصصة
  (`merged49`، `quota49`، `channel_dedupe_app`) **دون تسجيله في
  `sys.modules`**. لذلك: أي وحدة جديدة تُستورد `fb_youtube_uploader_v49_pro`
  بشكل مؤجّل (`_app_hook`) ستحصل على نسخة مختلفة غير مخترَقة من
  `monkeypatch` — وهذا يكسر الاختبارات.
- القاعدة: كل ما تختبره الاختبارات عبر `setattr(app, ...)` أو
  `app.<اسم>` ويترتب عليه سلوك داخلي (الحالة، `pt_now`، `QuotaManager`،
  منع التكرار) **يبقى في الملف الرئيسي**. الوحدات المستخرجة
  (`upload_pipeline`، `media_processing`) نجحت لأن اختباراتها تخترق
  الوحدة نفسها مباشرة (`pipe.MediaFileUpload`).
- عند استخراج دالة: أبقِ إعادة تصدير بالاسم نفسه في الملف الرئيسي
  (`from x import y`) وحدّث الاختبارات التي تخترق الموقع الجديد، وشغّل
  `./run_tests.sh` قبل وبعد.

## التشغيل والتحقق

```bash
./run_tests.sh                # pytest كامل + ruff
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python fb_youtube_uploader_v49_pro.py --preflight   # فحص المتطلبات
.venv/bin/python fb_youtube_uploader_v49_pro.py --help
```
- `requirements-transcribe.txt`: متطلبات اختيارية (`faster-whisper`) خارج
  `requirements.txt`؛ التفريغ الصوتي المحلي ميزة اختيارية وتعطّلها لا يمنع
  التشغيل. لا تُعِدها إلى `requirements.txt` كي لا يفشل تثبيت البيئة الأساسية
  على أجهزة بلا GPU/نموذج.

- CI (.github/workflows/ci.yml): مصفوفة Windows/Linux × Python 3.11/3.12/3.14،
  مع `pip check` و`pip-audit` على `requirements-lock.txt`. ملف القفل
  يستخدم علامات إصدار Python لـ `av` و`numpy` فقط (انظر «الحالة الحالية»).
- التغليف (docs/packaging.md): حزمة PyInstaller one-dir عبر
  `facbock-yo.spec` (`build_windows.bat` / `build_linux.sh`)، ومثبّت
  Inno Setup (`installer/facbock-yo.iss`) يثبّت للمستخدم
  (`{localappdata}\Programs\facbock.yo`) لا لـ Program Files، وسير عمل
  إصدار (`.github/workflows/release.yml`) يبني المثبّت على windows-latest
  عند وسم `v*`. قاعدة حرجة: في حزمة one-dir يشير `__file__` إلى داخل
  `_internal`، لذلك تُحلّ `APP_DIRECTORY` من `sys.executable` في الوضع
  المجمّد (`_resolve_app_directory`)، وملفات البيانات المجمّعة تسكن
  `_internal` بينما الأمثلة تُنسخ أيضاً إلى جذر الحزمة. التفريغ المحلي
  (`faster-whisper`) مستثنى من الحزمة قصداً؛ وFFmpeg خارجي عبر `PATH`.
- متطلبات على جهاز المستخدم لا يمكن محاكاتها هنا: متصفح Chrome،
  `client_secrets.json` (OAuth من Google Cloud)، ورمز حساب YouTube
  (`<account>_token.json`)، وكوكيز فيسبوك اختيارية في `[WATCH]`.

## الحالة الحالية

- الإصدار الحالي: **4.49.28** (إصلاح عطل إنتاجي على Windows: طرفية
  cp1252 كانت تُسقط التطبيق بـ `UnicodeEncodeError` عند طباعة النص
  العربي والسهم (→) — `--version` و`--help` و`--preflight` كلها كانت
  تنهار في النسخة المجمّدة؛ الآن `console_encoding.py` يعيد تهيئة
  stdout/stderr إلى UTF-8 مع `errors="replace"` في بداية `main()` لكل
  نقطة دخول (الملف الرئيسي و`doctor.py` و`enable_automation.py`)، مع
  11 اختبار انحدار جديداً تشغّل نقاط الدخول بترميز cp1252 صارم،
  وتشديد خطوة فحص الدخان في `release.yml` بفحص صريح لرموز خروج
  `--version` و`--help`. المجموعة الكاملة 799 ناجحاً واختباران متخطيان
  تحت 3.12.3 و3.11.18). الإصدار السابق **4.49.27** (إصلاح فشل CI على Windows فقط: اختبارات
  المسارات المجمّدة كانت تستخدم مسارات POSIX ثابتة ("/tmp/bundle") لا
  تصح على Windows، واختبار فحص الجاهزية كان ينشئ `facbock-yo-doctor`
  بلا لاحقة `.exe` التي يتطلبها Windows؛ الآن المسارات من `tmp_path`
  واللاحقة حسب المنصة، مع 4 اختبارات محاكاة Windows جديدة تثبّت السلوك
  على أي منصة. المجموعة الكاملة 788 ناجحاً واختباران متخطيان تحت
  3.12.3 و3.11.18). الإصدار السابق **4.49.26** (تغليف للتثبيت: حزمة PyInstaller one-dir
  ومثبّت Inno Setup للمستخدم وسير عمل إصدار؛ وإصلاح مسارات النسخة
  المجمّدة — `APP_DIRECTORY` من المنفّذ لا من `_internal`، وفحص الجاهزية
  يشغّل `facbock-yo-doctor` المجمّد، وتعطيل تحديث yt-dlp الذاتي في
  النسخة المثبّتة. 8 اختبارات مسارات مجمّدة و10 اختبارات تغليف جديدة؛
  المجموعة الكاملة 784 ناجحاً واختباران متخطيان تحت 3.12.3). الإصدار
  السابق **4.49.25** (جولة تدقيق تقوية: إصلاح الحالة البنيوي
  مع سجل الحقوق، بصمة مطالبة AI، دعم socks4، رفض VIDEO_DIRECTORY
  الملفّي، DNS وكيل محدود، doctor للروابط الرمزية، خطأ كتابة
  enable_automation، تحقق AI.MODEL، قص Shorts بأبعاد فردية، كشف المدة
  للملفات المبتورة، find_hot_segment للصمت، أرشيف يحتفظ بالأحدث، إيقاف
  بعد انتظار الحصة، ومقاطعة التفريغ بعد تنزيل النموذج. 33 اختباراً
  جديداً؛ المجموعة الكاملة 766 ناجحاً واختباران متخطيان تحت 3.12.3
  و3.11.18). الإصدار السابق **4.49.24** (إصلاح فشل CI في 4.49.23: اختبار
  `test_cli_preflight_uses_fingerprinted_oauth_token` كان يعتمد على
  `config.ini` المحيط، والفحص المسبق صار يستدعي `validate_configuration`
  منذ 4.49.23 فترفض النسخة النظيفة بلا ملف إعدادات برمز 2؛ الاختبار الآن
  يحاكي `validate_configuration`، مع اختبار انحدار يثبّت أن الإعدادات
  الفارغة تُرفض برمز 2 برسالتَي `VIDEO_DIRECTORY` و`CLIENT_SECRETS_FILE`.
  المجموعة الكاملة 732 ناجحاً واختباران متخطيان تحت 3.12.3 و3.11.18 وفي
  نسخة نظيفة تحاكي CI). الإصدار السابق **4.49.23** (إغلاق تسريب رمز جلسة Facebook `xs` إلى
  السجلات والتقرير التشخيصي عبر تنقيح سطور كوكيز Netscape في
  `safe_logging.py`؛ ترتيب أرشفة الإيصالات — الأرشيف قبل تقليم النشط؛
  التحقق من الإعدادات `validate_configuration` في فحص CLI المسبق؛
  `HTTP 400` بجسم فارغ من Gemini يأخذ تبريد 10 دقائق بدل تعطيل المفتاح
  حتى منتصف الليل؛ أخطاء نظيفة تشير إلى `--repair-state` عند تلف ملفات
  الحالة في CLI و`enable_automation.py`. 13 اختباراً جديداً؛ المجموعة
  الكاملة 731 ناجحاً واختباران متخطيان تحت 3.12.3 و3.11.18). الإصدار
  السابق **4.49.22** (دعم Python 3.11 في CI بعد التحقق الفعلي
  بالمجموعة الكاملة تحت 3.11.18؛ ملف القفل يثبّت `av==18.1.0` و`numpy==2.4.6`
  على `< "3.12"` و`av==19.0.2` و`numpy==2.5.5` على `>= "3.12"` لأن
  التخفيض الكامل كان سيكسر 3.14 — `av==18.1.0` بلا عجلة cp314 عادية؛
  ترقيات آمنة في القفل وأدوات التطوير `pytest 9.2.0` و`ruff 0.17.5`؛
  اختبار إقلاع الواجهة نجح تحت `xvfb-run` في بيئة التطوير هذه). الإصدار
  السابق 4.49.21 (إصلاح فشل CI على Windows: اختبار أذونات
  `doctor` صار POSIX فقط لأن قسم الفحص 7.5 محروس بـ `os.name == "posix"`
  قصداً، مع اختبار محاكاة يثبت أن Windows لا يُنتج تحذير أذونات). الإصدار
  السابق 4.49.20 (مهلة DNS محدودة في `probe_service`،
  جدولة نشر لا تُسقط الدورة على حدود معكوسة أو طابع بلا منطقة زمنية، حارس
  SSRF لاستعادة `og:title`، تجاهل حمولات DoH غير القاموسية، فحص أذونات
  `doctor` في مجلد التطبيق، وحذف كود مكرر/ميت). الإصدار السابق 4.49.19
  (فرض أبعاد زوجية و`yuv420p` في مرشح الشعار إصلاحاً لفشل ffmpeg `-22`
  على أبعاد فردية مثل 851×1277، ورفض عناوين واجهة فيسبوك
  `Reel tile preview` كعناوين عامة). الإصدار السابق 4.49.18
  (`--preflight` في CLI يستخدم `_youtube_token_paths` فيفحص الرمز المبني
  بالبصمة بدل الاسم القديم). الإصدار السابق 4.49.17 (سكربت
  `enable_automation.py` لتجهيز الوضع الآلي من سطر الأوامر، وإبقاء نافذة
  `.bat` مفتوحة عند الخطأ).
  الإصدار السابق 4.49.16 (إصلاحات المراجعة الثالثة: حقن الخروج
  `App._exit_fn` برمز 3 عند إنهاء العامل الحيّ بدل `os._exit(0)` المضلّل،
  حارس سطر الملخص في `run_tests.sh` و`ci.yml` يمنع اعتبار التشغيل المنقطع
  نجاحاً، توحيد رقم الإصدار في الشعار و`README`).
  الإصدار السابق 4.49.15 (`f0117d5`): تصنيف `PERMISSION_DENIED` في
  `ai_client` كرفض صلاحيات لا حصة، ترويسة `# ruff: noqa: E402`، سطر إصدار
  من `version.py`، فحص إقلاع واجهة `gui_boot_smoke.py`. وقبلها 4.49.14
  (`2938b33`): غلق نافذة جبري، مصدر وحيد للإصدار، نقل `faster-whisper` إلى
  متطلبات اختيارية.
- التزامات التفكيك: `425b0d0`/`8215be3` (media_processing)،
  `6778dab` (upload_pipeline)، `193423a` (توثيق البنية).
- الملف الرئيسي ~8.1k سطراً بعد نزوله من 9.6k؛ القسم المتبقي الأكبر هو
  صنف `App` (واجهة Tkinter) وهو مقيد بتعاقد الاختبارات أعلاه، وقسم CLI
  مرتبط باختراق `build_cli_settings` على `app`.

## فحص إقلاع الواجهة

- `gui_boot_smoke.py`: سكربت مستقل للتحقق من إقلاع النافذة وإغلاقها
  نظيفاً. يعمل عبر `xvfb-run -a python gui_boot_smoke.py` (يفتح النافذة
  ثم يغلقها بعد ثانية ويطبع `GUI BOOT + CLEAN CLOSE: OK`).
- تحقَّق في 2026-11-18 داخل بيئة التطوير هذه: نجح تحت `xvfb-run` في
  `.venv` الحالي وفي بيئة جديدة مبنية من Python النظام — القيد البيئي
  القديم (تعارض Tcl/Tk 8.6.13 مقابل مكتبات النظام 8.6.12) لم يعد قائماً
  هنا؛ إن عاد الفشل على جهاز آخر فسببه بيئي وليس في كود المشروع.
