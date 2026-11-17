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

- CI (.github/workflows/ci.yml): مصفوفة Windows/Linux × Python 3.12/3.14،
  مع `pip check` و`pip-audit` على `requirements-lock.txt`.
- متطلبات على جهاز المستخدم لا يمكن محاكاتها هنا: متصفح Chrome،
  `client_secrets.json` (OAuth من Google Cloud)، ورمز حساب YouTube
  (`<account>_token.json`)، وكوكيز فيسبوك اختيارية في `[WATCH]`.

## الحالة الحالية

- الإصدار الحالي: **4.49.16** (إصلاحات المراجعة الثالثة: حقن الخروج
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

- `gui_boot_smoke.py`: سكربت مستقل يُشغَّل على جهاز حقيقي (وليس في بيئة
  التطوير هذه) للتحقق من إقلاع النافذة وإغلاقها نظيفاً. يعمل عبر
  `python gui_boot_smoke.py` بعد تشغيل الواجهة الرسومية مباشرة (يفتح
  النافذة ثم يغلقها بعد ثانية ويطبع `GUI BOOT + CLEAN CLOSE: OK`).
- قيد بيئي موثّق: لا يمكن تشغيل الواجهة داخل بيئة التطوير الحالية —
  `_tkinter` وPIL (`_imagingtk`) في `.venv` مبنيان ضد Tcl/Tk 8.6.13 بينما
  مكتبات النظام 8.6.12 (تعارض إصدار حتى مع `LD_LIBRARY_PATH` وتصحيح
  `init.tcl`). سبب الفشل بيئي وليس في كود المشروع.
