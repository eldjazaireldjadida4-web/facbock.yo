# التحقق النهائي — الإصدار 4.49.28

## الخلاصة

إصلاح عطل إنتاجي حقيقي على Windows: طرفية cp1252 كانت تُسقط النسخة
المجمّدة بـ `UnicodeEncodeError` عند طباعة النص العربي والسهم (→).
التشغيل 44340376582 لبناء مثبّت v4.49.27 نجح في البناء نفسه وفشل في
فحص الدخان فقط؛ أُعيد إنتاج العطل محلياً (قبل الإصلاح) وتأكيد إصلاحه
في نسخة نظيفة تحاكي CI (بعد الإصلاح).

## السبب الجذري

طرفية Windows الافتراضية cp1252 لا تمثّل العربية ولا السهم (→)،
والتطبيق يطبع كليهما في كل نقطة دخول:

1. `--version` يطبع `FB → YouTube Uploader ...` فينهار على `→` في
   الموضع 3 (تتبّع استثناء، رمز خروج 1).
2. `--help` يطبع نص المساعدة العربي فينهار على أحرف في المواضع 132-135.
3. `--preflight` يطبع تقرير الفحص المسبق العربي فينهار بالطريقة نفسها
   (رمز 1 بدل رمز 2 المتوقع «بيانات اعتماد مفقودة» في نسخة نظيفة).
4. `doctor.py` و`enable_automation.py` يطبعان العربية أيضاً فكانا
   معرَّضَين للانهيار نفسه. حزمة Linux لم تتأثر لأن stdout فيها UTF-8.

وساهم في إخفاء العطل أن خطوة فحص الدخان في `release.yml` كانت تفحص
رمز خروج `--preflight` فقط؛ وPowerShell لا يتوقف على رمز خروج غير
صفري لأمر أصلي، فمرّ انهيار `--version` و`--help` صامتاً في السجل.

## إعادة الإنتاج (قبل الإصلاح)

نسخة نظيفة (ملفات متعقَّبة وغير متعقَّبة كما على القرص، بلا
`config.ini`) مع `PYTHONIOENCODING=cp1252` — قبل تعطيل الإصلاح في
النسخة فقط (شجرة العمل لم تُمسّ):

```
--version:   UnicodeEncodeError: 'charmap' codec can't encode character '\u2192' in position 3
             exit=1
--preflight: exit=1, UnicodeEncodeError في stderr، وVIDEO_DIRECTORY غائب من الإخراج
```

ونسخة Linux المباشرة (قبل الإصلاح أيضاً):

```
PYTHONIOENCODING=cp1252 .venv/bin/python fb_youtube_uploader_v49_pro.py --version
  → UnicodeEncodeError: ... '\u2192' in position 3 (exit 1)
PYTHONIOENCODING=cp1252 .venv/bin/python fb_youtube_uploader_v49_pro.py --help
  → UnicodeEncodeError: ... position 132-135
```

## الإصلاح (كود إنتاج)

- وحدة جديدة `console_encoding.py`: `reconfigure_std_streams_utf8()`
  تعيد تهيئة `sys.stdout` و`sys.stderr` إلى UTF-8 مع
  `errors="replace"`، محروسة بـ `try/except` فلا يمكنها إيقاف الإقلاع
  أبداً (تيار مفقود أو بلا `reconfigure` أو فشل إعادة التهيئة كلها
  تُتخطَّى بصمت). الأحرف نفسها لم تُستبدل — الإخراج يبقى مقروءاً.
- تُستدعَى في بداية `main()` قبل أي تحليل أو طباعة في الملف الرئيسي
  (`fb_youtube_uploader_v49_pro.py`) و`doctor.py`
  و`enable_automation.py`.
- لا تغيير في بوابة الحقوق (تبقى `strict`)، ولا تقسيم للملف الرئيسي،
  و`version.py` يبقى المصدر الوحيد للإصدار.
- `.github/workflows/release.yml`: فحص صريح لرموز خروج `--version`
  و`--help` في خطوة فحص الدخان (7 أسطر مضافة). **هذا الملف لا يمكن
  دفعه عبر تطبيق GitHub (بلا صلاحية Workflows) — يجب على المستخدم
  تطبيق التغيير يدوياً على GitHub** (انظر «ملف يدوي» أدناه).

## الاختبارات الجديدة (11)

`tests/test_console_encoding.py` — تشغّل نقاط الدخول بترميز cp1252
صارم كما في طرفية Windows:

- 5 اختبارات وحدة للمُعيد: استدعاء `reconfigure` بـ
  `encoding="utf-8", errors="replace"` على التيارين، وتخطٍّ صامت
  لتيار بلا `reconfigure` (`io.StringIO`) وتيار `None` وتيار يرفع
  من `reconfigure`، وغلاف `TextIOWrapper` حقيقي بـ cp1252 صارم يقبل
  بعد الإصلاح `→` والعربية.
- 4 عمليات فرعية بـ `PYTHONIOENCODING=cp1252`: `--version` رمز 0 مع
  `FB → YouTube Uploader`، و`--help` رمز 0، و`doctor.py` رمز 0/1 بلا
  تتبّع، و`enable_automation.py --show` رمز 0.
- `--preflight` رمز 2 في نسخة نظيفة بلا `config.ini` (ملفات متعقَّبة
  وغير متعقَّبة كما على القرص — `git ls-files --cached --others
  --exclude-standard` — لا من HEAD، حتى تشمل الوحدة الجديدة)، مع
  `VIDEO_DIRECTORY` في الإخراج وبلا `UnicodeEncodeError`.
- فحص مسبق داخلي `main(["--preflight"])` مع `stdout/stderr‏`
  `TextIOWrapper` حقيقيين بـ cp1252 صارم: يرجع 2 ويطبع
  `VIDEO_DIRECTORY` و`CLIENT_SECRETS_FILE`.

ملاحظة تصحيحية أثناء كتابة الاختبار: النسخة الأولى من اختبار النسخة
النظيفة كانت تشغّل سكربت شجرة العمل (لا سكربت النسخة) فقرأت
`config.ini` المحيط وفشلت؛ وكانت تنسخ الملفات المتعقَّبة فقط فغابت
`console_encoding.py` الجديدة (`ModuleNotFoundError`). أُصلح
الاختبار نفسه (السكربت من النسخة، والنسخ يشمل غير المتعقَّبة) — لا
كود الإنتاج.

## التحقق (بعد الإصلاح)

| البيئة | النتيجة |
|---|---|
| نسخة نظيفة `PYTHONIOENCODING=cp1252` (قبل/بعد في التجربة الضابطة) | قبل: `--version` رمز 1 و`--preflight` رمز 1 مع `UnicodeEncodeError`؛ بعد: `--version` رمز 0 (`FB → YouTube Uploader v4.49.28 PRO`)، و`--help` رمز 0، و`--preflight` رمز 2 مع `VIDEO_DIRECTORY` وبلا `UnicodeEncodeError` |
| `.venv` Python 3.12.3 (مجلد المشروع) | 799 ناجحاً، 2 متخطيان |
| Python 3.11.18 (`~/.py311-test-venv`) | 799 ناجحاً، 2 متخطيان، 1 تحذير (سابق: `google.api_core` حول نهاية دعم 3.11) |
| الاختبارات الجديدة | 11 ناجحة |
| `ruff check .` / `compileall` | نظيف / ناجح |
| `--version` / `--help` / `--preflight` في النسخة النظيفة | 0 / 0 / 2 كما هو متوقع |

الاختباران المتخطيان هما اختبارا أذونات POSIX عند التشغيل كـ root
(متوقعان). لا يوجد مفسّر Python 3.14 محلياً؛ يحتاج تأكيد CI بعد الدفع
كالمعتاد. لم يُدفع شيء ولم يُوسم شيء — كل التغييرات غير مُثبَّتة
بانتظار التحقق والموافقة.

## ملف يدوي: ما يجب على المستخدم إضافته على GitHub

`.github/workflows/release.yml` — في خطوة `Smoke-test the frozen app`
بعد سطر `.\\dist\\facbock-yo\\facbock-yo.exe --version` أضف:

```yaml
          # PowerShell does NOT stop on a native command's non-zero exit
          # code, so every smoke-test command needs an explicit check:
          # in 4.49.27 --version/--help crashed (UnicodeEncodeError on the
          # cp1252 Windows console) and the job still went on to fail only
          # at --preflight, hiding which step broke.
          .\dist\facbock-yo\facbock-yo.exe --version
          if ($LASTEXITCODE -ne 0) { throw "facbock-yo --version exit was $LASTEXITCODE, expected 0" }
          .\dist\facbock-yo\facbock-yo.exe --help
          if ($LASTEXITCODE -ne 0) { throw "facbock-yo --help exit was $LASTEXITCODE, expected 0" }
```

(الأسطر نفسها موجودة محلياً في الملف؛ يكفي نسخ المقطع كاملاً إلى
الملف على GitHub كما فُعل مع `ci.yml` و`release.yml` من قبل.)

## ملفات متغيرة

- `console_encoding.py` (جديد): المُعيد `reconfigure_std_streams_utf8()`.
- `fb_youtube_uploader_v49_pro.py`: استدعاء المُعيد في بداية `main()` +
  الترويسة إلى 4.49.28.
- `doctor.py` و`enable_automation.py`: استدعاء المُعيد في بداية `main()`.
- `.github/workflows/release.yml`: فحص صريح لرموز خروج `--version`
  و`--help` (يدوي على GitHub).
- `tests/test_console_encoding.py` (جديد): 11 اختبار انحدار.
- `tests/test_hardening_44920.py`: اختبار اتساق الإصدار إلى 4.49.28.
- `version.py` و`README.md` و`AGENTS.md` و`CHANGELOG.md`
  و`installer/facbock-yo.iss` و`docs/packaging.md`: رقم الإصدار 4.49.28.
- `docs/validation/FINAL_VALIDATION_4.49.28.md`: هذا الملف.
