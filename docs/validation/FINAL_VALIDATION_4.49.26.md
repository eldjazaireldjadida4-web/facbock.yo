# التحقق النهائي — الإصدار 4.49.26

## الخلاصة

حزمة تثبيت (PyInstaller one-dir + مثبّت Inno Setup + سير عمل إصدار
GitHub)، مع إصلاح حقيقي لمسارات النسخة المجمّدة اكتُشف أثناء التحقق من
الحزمة نفسها. لا تغيير في سلوك التشغيل من المصدر.

## المشكلة التي وجدها التحقق (قبل الإصلاح)

في حزمة PyInstaller one-dir يشير `__file__` إلى داخل مجلد `_internal`
للمكتبات، فكان `APP_DIRECTORY` هو `_internal`:

- `config.ini` في مجلد التثبيت تُجاهل تماماً (أُثبت بوضعها في الجذر
  ثم داخل `_internal`: قُرئت من `_internal` فقط).
- فحص الجاهزية فشل برسالة «لم يُعثر على أداة التشخيص:
  .../_internal/doctor.py».
- المنفّذ `facbock-yo-doctor` أبلغ كذباً عن مكتبات مفقودة
  (arabic-reshaper, python-bidi, yt-dlp, google-auth-httplib2,
  portalocker) لأن تحليل PyInstaller لـ `doctor.py` وحده لا يصل إليها،
  مع أن التطبيق نفسه يستوردها.

## الإصلاح (كود إنتاجي + تغليف)

- `APP_DIRECTORY` تُحلّ من المنفّذ (`sys.executable`) في الوضع المجمّد
  في الملف الرئيسي (`_resolve_app_directory`) و`doctor.py`
  و`enable_automation.py` (`_resolve_app_dir`)؛ ومن الملف المصدري في
  وضع المصدر.
- `control_plane.run_readiness_check` يشغّل المنفّذ المجمّد
  `facbock-yo-doctor` في الوضع المجمّد، و`doctor.py` في وضع المصدر.
- زر فحص الجاهزية في الواجهة يمرّر `APP_DIRECTORY` بدل إعادة حسابه من
  `__file__` (كان يتجاوز الإصلاح).
- تحديث yt-dlp الذاتي معطّل في النسخة المثبّتة برسالة نظيفة.
- مراجعة المحتوى تعيد `content_review_python_not_found` بدل تشغيل
  التطبيق نفسه كمفسّر في الوضع المجمّد.
- ملف spec: `DOCTOR_HIDDEN_IMPORTS` يحمل كل مكتبات
  `dependency_check.RUNTIME_IMPORTS` لمنفّذ doctor (اختبار يفرض
  المزامنة)؛ واستثناءات مكدّس faster-whisper؛ و`console=True`؛ وثلاثة
  منفّذات في COLLECT واحد.
- التغليف: `facbock-yo.spec`، و`build_windows.bat` (LF)، و`build_linux.sh`،
  و`installer/facbock-yo.iss` (تثبيت للمستخدم، `PrivilegesRequired=lowest`،
  بلا أسرار)، و`.github/workflows/release.yml` (يبني المثبّت على
  windows-latest ويرفعه أصل إصدار عند وسم `v*`)، و`docs/packaging.md`.
- التفريغ المحلي مستثنى من الحزمة قصداً؛ وFFmpeg خارجي عبر `PATH`.

## التحقق

| الفحص | النتيجة |
|---|---|
| المجموعة الكاملة، `.venv` Python 3.12.3 | **784 ناجحاً، 2 متخطيان** |
| المجموعة الكاملة، Python 3.11.18 (`~/.py311-test-venv`) | **784 ناجحاً، 2 متخطيان**، تحذير واحد سابق (`google.api_core` حول انتهاء 3.11) |
| `ruff check .` / `compileall` / `pip check` / `pip-audit` | نظيف / ناجح / نظيف / بلا ثغرات معروفة |
| `--version` | v4.49.26 |
| بناء Linux (`build_linux.sh` من الصفر) | حزمة 282M، أرشيف 98M |
| المنفّذ المجمّد `--version` / `--help` | رمز 0؛ بدء بارد ≈ 2.3 ثانية |
| المنفّذ المجمّد `--preflight` في مجلد نظيف | رمز 2 (بيانات اعتماد مفقودة) مع تقرير نظيف، وقسم doctor يعمل |
| `config.ini` في جذر الحزمة | تُقرأ بعد الإصلاح (كانت تُتجاهل قبله) |
| المنفّذ `facbock-yo-doctor` | «مكتبات Python — كل المكتبات المطلوبة موجودة»؛ الباقي فشلان حقيقيان فقط (Chrome غير مثبّت على هذه الآلة، و`client_secrets.json` مفقود في مجلد نظيف) |
| المنفّذ `facbock-yo-automation --help` | رمز 0 |
| الواجهة المجمّدة تحت Xvfb (عبر `facbock-yo.sh`) | بقيت تعمل 15 ثانية بلا تتبّع استثناء؛ وأنشأ المشغّل `config.ini` بصلاحيات 600 من المثال |
| الحزمة: مكدّس التفريغ / الأسرار | غائب تماماً (لا onnxruntime/faster-whisper، لا config.ini ولا رموز) |
| `release.yml` | YAML صالح؛ لم يُدفع (يحتاج إضافة يدوية على GitHub — تطبيق Muse بلا صلاحية Workflows) |
| Python 3.14 | **غير متحقق محلياً** — لا مفسّر 3.14 هنا؛ يحتاج CI بعد الدفع كالمعتاد؛ ومثبّت Windows نفسه لم يُبنَ هنا (لا Windows في هذه الآلة) بل يبنيه سير عمل الإصدار |

## ملفات متغيرة

- كود: `fb_youtube_uploader_v49_pro.py` (الترويسة + `_resolve_app_directory`
  + موقعا الاستدعاء + حارس yt-dlp)، و`doctor.py`، و`enable_automation.py`،
  و`control_plane.py`، و`content_review_bridge.py`.
- إصدار وتوثيق: `version.py` و`README.md` و`AGENTS.md` و`CHANGELOG.md`
  و`tests/test_hardening_44920.py` (اختبار الاتساق إلى 4.49.26).
- تغليف (جديد): `facbock-yo.spec`، و`build_windows.bat`، و`build_linux.sh`،
  و`installer/facbock-yo.iss`، و`.github/workflows/release.yml`، و`docs/packaging.md`.
- اختبارات (جديدة): `tests/test_frozen_paths.py` (8) و`tests/test_packaging.py` (10).
- `.gitignore`: استثناء `.venv-build/` و`installer-output/` (مخرجات بناء
  محلية لا تدخل المستودع).
