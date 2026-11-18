# التحقق النهائي — الإصدار 4.49.25

## الخلاصة

جولة تدقيق تقوية من ثلاث مراجعات للقراءة فقط وجدت 15 مشكلة حقيقية
أُعيد إنتاجها، وأُصلحت كلها مع 33 اختباراً جديداً في
`tests/test_hardening_44925.py`. المجموعة الكاملة 766 ناجحاً واختباران
متخطيان تحت Python 3.12.3 و3.11.18، و`ruff` نظيف، و`compileall` ناجح،
و`pip check` و`pip-audit` نظيفان، واختبار إقلاع الواجهة ناجح تحت
`xvfb-run`. لا يوجد مفسّر Python 3.14 محلياً؛ يحتاج تأكيد CI بعد الدفع
كالمعتاد.

## الأسباب الجذرية والإصلاحات

### 1. repair-state أعمى عن التلف البنيوي (HIGH)

- `_ledger_state` كان يكتفي بأن ملف JSON يُقرأ ونوعه العلوي صحيح، بينما
  المحمّلات ترفض أكثر من ذلك: الإيصالات تحتاج `schema_version == 1`
  ومعرّف YouTube صالحاً و`source_id` مطابقاً للمفتاح، وعناصر قائمة
  الفشل تحتاج `id` و`url` غير فارغين، والحسابات سلاسل غير فارغة.
  أُعيد إنتاجه: `_ledger_state` يقول "ok" بينما `load_upload_receipts`
  يرفع `RuntimeError`، و`repair_state_files()` يعيد `[]`.
- الإصلاح: كل مدخل في `STATE_LEDGERS` صار رباعياً مع مدقّق بنيوي يطابق
  فحوص المحمّل حرفياً (`_validate_receipts_store`،
  `_validate_failed_queue`، `_validate_accounts`؛ و`pages.json`/
  `state.json`/`channel_video_index.json` تبقى فحص النوع العلوي لأن
  محمّلاتها تتسامح مع المدخلات التالفة وتسقطها). `_ledger_state`
  يستدعي المدقّق على الأساسي ثم `.bak1`-`.bak3`؛ أساسي تالف مع نسخة
  صالحة يعطي `backup_ok` لا عزلاً كما قبل.

### 2. rights_manifest.json خارج STATE_LEDGERS (MEDIUM)

- `content_safety.get_rights_record` يرفع `ValueError` على سجل تالف،
  لكن `repair_state_files()` يعيد `[]`.
- الإصلاح: مدخل جديد في `STATE_LEDGERS` بمدقّق يطابق
  `content_safety._read_manifest_file` (قاموس مع `items` قاموس).
  `load_rights_manifest` نفسه لا يصلح مدقّقاً لكل مرشّح: يقبل مساراً لا
  قيمة، ويسقط تلقائياً إلى `.bak` عند تلف الأساسي فيخفي تلف المرشّح.
  `_repair_state_cli` صار يحذّر لعزل سجل الحقوق كما يحذّر للإيصالات.

### 3. ذاكرة AI تتجاهل العنوان/الوصف/الأسلوب/النموذج (MEDIUM)

- `App._ai_metadata` كان يكتفي بمطابقة هاش النص المفرّغ، فعنوان مصحح
  أو أسلوب/نموذج متغيّر يعيدان استخدام وصف الجيل الأول ووسومه بصمت.
- الإصلاح: بصمة SHA-256 لكل مدخلات المطالبة (العنوان، الوصف، النموذج،
  الأسلوب، `preserve_title`، وتلميح الأداء) تُحفظ في `_prompt_hash`
  إلى جانب `_transcript_hash`، ولا إصابة إلا بمطابقتهما معاً. سلوك هاش
  النص الذي يثبّته الاختبار القديم باقٍ؛ مدخلات ذاكرة قديمة بلا
  `_prompt_hash` تُولَّد مرة واحدة ثم تُحفظ بالبصمة الجديدة.

### 4. socks4 يمرّ التحقق ويُرفض عند التشغيل

- `settings_validation.py:158` كان يقبل `socks4`، بينما
  `facebook_scraper.py:149` و`diagnostic_report.py:254` و`_selenium_proxy_server`
  يرفضونه، والمنفذ الافتراضي لمخططات socks كان 80 لا 1080 في بعضها.
- الإصلاح بدعم socks4 لا برفضه (yt-dlp وChrome يتحدثانه): أُضيف
  `"socks4"` إلى مجموعات المخططات الثلاث، والمنفذ الافتراضي صار
  `1080` لكل `scheme.startswith("socks")`، ورسالة خطأ Selenium ونص
  المساعدة في الواجهة يذكران socks4. اختبار يثبت اتفاق
  `validate_configuration` و`facebook_network_route_detailed` على كل
  مخطط في `{"http"، "https"، "socks4"، "socks5"، "socks5h"}`.

### 5. VIDEO_DIRECTORY باسم ملف عادي يُسقط التطبيق عند الاستيراد

- `os.makedirs(VIDEO_DIRECTORY, exist_ok=True)` عارٍ عند الاستيراد؛
  ملف عادي بذلك الاسم يرفع `FileExistsError` قبل أي تحقق أو إصلاح،
  حتى `--help` و`--repair-state` يموتان.
- الإصلاح بطبقتين: `validate_configuration` يرفض المسار الموجود
  وليس مجلداً، و`makedirs` عند الاستيراد ملفوف بـ `try/except
  (OSError, ValueError)` يطبع رسالة نظيفة تسمّي `VIDEO_DIRECTORY`
  ويخرج برمز 2، على نمط معالج `ConfigRecoveryError` المجاور.

### 6. DNS فرع الوكيل بلا حدّ

- فرع الوكيل كان يستدعي `socket.create_connection((hostname, port),
  timeout=timeout)`؛ المهلة تقيّد TCP فقط و`getaddrinfo` داخلها بلا
  مهلة. أُعيد إنتاجه: مهلة 5 ثوانٍ استغرقت 30 ثانية مع محلّل متعطل.
- الإصلاح: الحلّ أولاً عبر `_resolve_bounded` الموجود (نصف المهلة)
  ثم `create_connection` إلى العنوان المحلول (النصف الآخر)، كما يقسّم
  `diagnostic_report.probe_service` زمن الحلّ/الاتصال. اختبار بمحاكاة
  `getaddrinfo` بطيئة (30 ثانية) يثبت أن المنقضي يبقى ضمن المهلة + هامش.

### 7. doctor يمرّر config.ini رمزياً يرفضه التطبيق

- `doctor._load_config` كان يستخدم `open()` عادياً (يتّبع الروابط)
  وقسم 9 يستخدم `os.path.exists`، بينما `config_store.read_config`
  يرفض الروابط ويرفع `ConfigRecoveryError` فيخرج التطبيق برمز 2.
- الإصلاح: رفض الرابط الرمزي في `doctor._load_config` وفي
  `diagnostic_report._load_config` بنص `config_store` نفسه
  (`symbolic-link configuration files are not accepted`)، وقسم 9
  يتطلب `isfile() and not islink()` مع صف BAD مخصصة للرابط الرمزي.

### 8. doctor يعدّ مجلداً باسم `*_token.json` ربط يوتيوب

- قائمة التوكنات كانت من `os.listdir` بلا فحص نوع.
- الإصلاح: ترشيح بـ `os.path.isfile(os.path.join(app_directory, n))`.

### 9. فشل كتابة enable_automation بلا معالجة

- `main()` كان يلتقط `ConfigRecoveryError` فقط؛
  `write_config_atomically` قد يرفع `OSError` (قرص ممتلئ/مجلد للقراءة
  فقط) فيتتبّع الاستثناء.
- الإصلاح: `try/except OSError` حول استدعاء التطبيق في `_main_inner`
  يطبع «تعذرت كتابة config.ini: …؛ لم يُطبَّق شيء» ويعيد 2. الكتابة
  ذرية فالملف يبقى سليم البنية.

### 10. AI.MODEL بلا تحقق (low)

- كان يُتحقق من `AI.TRANSCRIPTION_MODEL` فقط؛ نموذج قمامة يجعل كل
  نداء Gemini يفشل بـ 404.
- الإصلاح: تحقق `AI.MODEL` مقابل `GEMINI_MODELS` من `ai_client`
  باستيراد مؤجّل داخل الدالة (لا دورة استيراد: `ai_client` لا يعتمد
  على `settings_validation`).

### 11. cut_shorts_clip يفشل على أي فيديو بأبعاد فردية

- إعادة الترميز بـ `-c:v libx264` بلا ضمان أبعاد زوجية/`yuv420p`، رغم
  أن مسار تركيب الشعار حصل على الإصلاح نفسه في 4.49.19. أُعيد إنتاجه:
  فيديو 851×481 يجتاز `validate_media` لكن القص يعيد `None`
  («width not divisible by 2 … error code -22»).
- الإصلاح: `-vf scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p`
  قبل `-c:v libx264`. اختبار حقيقي بـ ffmpeg على 851×481 يخرج
  850×480.

### 12. finalize_video / apply_logo_overlay يقصّران ملفاً مبتوراً بصمت

- كانا يفحصان رمز خروج ffmpeg وحجم الإخراج فقط لا المدة؛ ملف faststart
  مبتور يجتاز `validate_media` (ffprobe يعلن مدة الترويسة) ويخرج أقصر
  بنحو 49% فيُرفع «ناجحاً». أُعيد إنتاجه: ملف 10 ثوانٍ مقطوع إلى 55%
  من بايتاته يعلن ffprobe مدته 10 ثوانٍ، ونسخة `-c copy` منه 5.53 ثانية.
- الإصلاح: مساعد مشترك `media_processing.durations_match` (تفاوت
  `max(1 ثانية، 2% من مدة المصدر)`)؛ `finalize_video` يرفع
  `PipelineFailure` برمز `output_duration_mismatch` (ويحذف الإخراج)،
  و`apply_logo_overlay` يعيد `None` مع رسالة مدة. مدة مصدر غير قابلة
  للقياس (0) تعني تعذّر المقارنة فلا تُرفض.

### 13. find_hot_segment يعدّل على الثواني الحاضرة فقط

- `avg = sum(window) / len(window)` على الحاضرة فقط؛ ذروة ثانية واحدة
  (-10) كانت «تفوز» بنافذة 58 ثانية معظمها غائب (متوسط -10 مقابل
  نوافذ كثيفة بمتوسط أدنى).
- الإصلاح: الغائب صمت (`-91.0`، قيمة `-inf` نفسها) والقسمة على
  `clip_seconds` كاملاً. حارس `if not rms_by_second: return None`
  يحفظ الاختبار المثبّت للمدخل الفارغ (كل النوافذ غائبة كان متوسطها
  -91.0 فيعيد (0، 58) بدل None بدونه). اختبار جديد: ذروة وحيدة تخسر
  أمام 30 ثانية كثيفة (-40).

### 14. compact_upload_receipts يسقط الإيصال الأحدث عند إعادة الرفع

- `archive.setdefault(...)` كان يُبقي مدخل الأرشيف القديم؛ عند
  التقليم الثاني يختفي الإيصال الأحدث من الملفين معاً.
- الإصلاح: عند الدمج، الأحدث يحلّ محل الأقدم بمقارنة
  `_receipt_timestamp` (وهي تسقط إلى `rejected_at` عند غياب
  `confirmed_at`)؛ بلا طابعين يبقى القديم. حُدّث الاختبار القديم
  `test_archive_merge_never_overwrites_an_existing_entry` الذي كان
  يثبّت السلوك الخاطئ إلى اختبارين: الأحدث يحلّ محل الأقدم، والأقدم
  لا يمحو الأحدث.

### 15. الإيقاف يُتجاهل بعد انتظار الحصة مع المراجعة مفعّلة

- `_wait_for_quota_reset` لم يستشر `self._stop`: `_countdown_wait`
  يعود مبكراً عند الإيقاف، ثم يستدعي الأسلوب `quota._refresh()` ويعود
  عادياً، فيمضي `_run_cycle` إلى `run_policy_review` (حتى 5400 ثانية)
  قبل أن يوقف `upload_video` العنصر.
- الإصلاح: الأسلوب يعيد `False` عند طلب الإيقاف و`True` غير ذلك،
  وموقع ما قبل المراجعة يعيد العنصر وباقي القائمة (`process_list[index
  - 1:]`) ويخرج قبل المراجعة؛ وموقع `QUOTA_ERROR` يعيد الباقي
  (`process_list[index:]`) ويخرج كذلك. سلوك المسار بلا مراجعة باقٍ
  كما هو (الخروج قبل المراجعة لا يغيّر شيئاً فيه).

### 16. تنزيل نموذج التفريغ المحلي غير قابل للمقاطعة

- كان `stop_event` يُفحص قبل `_load_model` وبين المقاطع فقط، لا أثناء
  تنزيل النموذج الأول (مئات الميغابايت، بلا مهلة) تحت `_MODEL_LOCK`.
- الإصلاح: فحص `stop_event` فور عودة `_load_model` قبل بدء حلقة
  المقاطع، مع تعليق يوثّق أن التنزيل الجاري لا يمكن إلغاؤه أثناء
  تدفّقه. الاختبار يحاكي `_load_model` (لا تنزيل حقيقي) ويثبت أن
  `transcribe` لا يُستدعى أبداً عند ضبط الحدث.

## اختبارات محدَّثة كانت تثبّت السلوك القديم

- `tests/test_hardening_4494.py`: دمج الأرشيف (انظر §14).
- `tests/test_facebook_scraper.py::test_proxy_preflight_uses_http_default_port_and_honors_explicit_port`:
  كان يثبّت اتصال `create_connection` باسم المضيف؛ الاتصال الآن يتم
  إلى العنوان المحلول بعد `_resolve_bounded`، فالاختبار يحاكي
  `getaddrinfo` ويثبت المنفذين الافتراضي (80) والصريح (3128) على
  العنوان المحلول.
- `tests/test_merge_v49.py::test_finalize_video_success_returns_cleaned`:
  كان يحاكي `subprocess.run` بإرجاع بلا `stdout`؛ `finalize_video`
  صار يستدعي `ffprobe` عبره، فالمحاكاة تجيب نداءات ffprobe بمدة 10
  ثوانٍ مطابقة.

## إعادة الإنتاج (قبل الإصلاح)

- ملفات بنيوية تالفة: `_ledger_state` يقول "ok" و`repair_state_files()`
  يعيد `[]` بينما المحمّلات ترفع `RuntimeError`.
- `socks4://proxy:1080` يجتاز `validate_configuration` ويرفضه فحص
  الشبكة وSelenium.
- مهلة وكيل 5 ثوانٍ مع محلّل متعطل: 30 ثانية فعلية.
- فيديو 851×481: قص Shorts يعيد `None` برمز `-22`.
- ملف 10 ثوانٍ مقطوع إلى 55% من بايتاته: `validate_media` يمرّ،
  ونسخة `-c copy` تخرج 5.53 ثانية.
- تقليم ثانٍ بعد إعادة رفع: الإيصال الأحدث يختفي من الملفين.

## التحقق (بعد الإصلاح)

| البيئة | النتيجة |
|---|---|
| `.venv` Python 3.12.3 (مجلد المشروع) | 766 ناجحاً، 2 متخطيان |
| Python 3.11.18 (`~/.py311-test-venv`) | 766 ناجحاً، 2 متخطيان، تحذير واحد |
| `ruff check .` | نظيف |
| `python -m compileall -q .` | ناجح |
| `python -m pip check` | لا اعتماديات مكسورة |
| `.venv/bin/pip-audit -r requirements-lock.txt` | لا ثغرات معروفة |
| `--version` | v4.49.25 |
| `xvfb-run -a .venv/bin/python gui_boot_smoke.py` | GUI BOOT + CLEAN CLOSE: OK |

الأساس قبل التغييرات كان 732 ناجحاً واختباران متخطيان؛ الزيادة 34
اختباراً = 33 جديداً في `tests/test_hardening_44925.py` + اختبار أرشيف
إضافي من تقسيم الاختبار القديم. الاختباران المتخطيان هما اختبارا
أذونات POSIX عند التشغيل كـ root (متوقعان). التحذير الواحد تحت 3.11 هو
`FutureWarning` قديم من `google.api_core` عن قرب انتهاء دعم Python
3.11 (2027-10-24)، لا علاقة له بهذا الإصدار. لا يوجد مفسّر Python
3.14 محلياً؛ يحتاج تأكيد CI بعد الدفع كالمعتاد. لا ترقيات اعتماديات
متاحة (الأقفال عند أحدث الإصدارات أصلاً).

## ملفات متغيرة

- `version.py` و`fb_youtube_uploader_v49_pro.py` (الترويسة + الإصلاحات
  1–3 و5 و14 و15) و`README.md` و`AGENTS.md` و`CHANGELOG.md`: رقم
  الإصدار 4.49.25 والتوثيق.
- `settings_validation.py`: رفض VIDEO_DIRECTORY الملفّي وتحقق AI.MODEL.
- `facebook_scraper.py`: دعم socks4 وDNS محدود لفرع الوكيل.
- `diagnostic_report.py`: دعم socks4 في `_proxy_endpoint` ورفض
  config.ini الرمزي في `_load_config`.
- `doctor.py`: رفض config.ini الرمزي وترشيح مجلدات التوكن.
- `enable_automation.py`: خطأ نظيف عند فشل الكتابة.
- `media_processing.py`: مرشح أبعاد قص Shorts، فحص مدة الشعار،
  `find_hot_segment`، والمساعد `durations_match`.
- `upload_pipeline.py`: فحص مدة `finalize_video`.
- `local_transcription.py`: فحص الإيقاف بعد تحميل النموذج.
- `tests/test_hardening_44925.py`: 33 اختباراً جديداً.
- `tests/test_hardening_44920.py`: اختبار اتساق الإصدار إلى 4.49.25.
- `tests/test_hardening_4494.py` و`tests/test_facebook_scraper.py`
  و`tests/test_merge_v49.py`: تحديث 3 اختبارات كانت تثبّت السلوك القديم.
- `docs/validation/FINAL_VALIDATION_4.49.25.md`: هذا الملف.
