# التحقق النهائي — الإصدار 4.49.29

## الخلاصة

إصلاح فشل CI على Windows بعد رفع 4.49.28: التشغيل 44344184622 فشل
في الوظائف الثلاث على Windows (Ubuntu أخضر) باختبار واحد فقط —
`test_preflight_exits_2_in_clean_copy_under_strict_cp1252`
(`assert 1 == 2`). عملية `--preflight` الفرعية في نسخة نظيفة انهارت
بـ `AttributeError: 'NoneType' object has no attribute 'strip'` في
`run_readiness_check` عند جمع مخرجات الطبيب. السبب ليس في فحص
الجاهزية نفسه ولا في إصلاح cp1252؛ بل في افتراض أن `subprocess.run`
مع `capture_output=True` يعيد `stdout` نصاً دائماً — على Windows في
CI أعاده `None`.

## السبب الجذري

`control_plane.py: run_readiness_check` كان يستدعي
`result.stdout.strip()` مباشرةً. في وظائف Windows الثلاث لتشغيل
44344184622 أعاد تشغيل `doctor.py` الفرعي `stdout=None` (رغم
`capture_output=True`)، فرفع `AttributeError` قبل أن يصل الفحص
المسبق إلى `validate_configuration` الذي كان سيفرض رمز 2، فخرجت
العملية برمز 1. سجلّ CI نفسه لا يحوي التتبع الكامل (pytest يختصر
تمثيل `CompletedProcess`)، لكن ذيله الظاهر ينهي بـ
`AttributeError: 'NoneType' object has no attribute 'strip'` بعد
`.stdout.strip()`، ولا يظهر `UnicodeEncodeError` في السجل إطلاقاً —
فإصلاح 4.49.28 نفسه سليم.

## إعادة الإنتاج (قبل الإصلاح)

محلياً على Linux تمرّ النسخة النظيفة (رمز 2) لأن `stdout` لا يكون
`None` هناك؛ العطل Windows فقط. أُعيد إنتاجه بمحاكاة مباشرة: تشغيل
`run_readiness_check` مع `subprocess.run` مزيفة تعيد
`CompletedProcess(returncode=1, stdout=None, stderr="...")` — قبل
الإصلاح ترفع `AttributeError`، وبعده ترجع `(1, "doctor boom")` دون
رفع. واختبار النسخة النظيفة نفسه كان يخفي السبب: تأكيده بلا رسالة
فأظهر `assert 1 == 2` فقط دون مخرجات العملية الفرعية.

## الإصلاح (كود إنتاج)

- `control_plane.py: run_readiness_check`:
  - `output = (result.stdout or "").strip()` و`(result.stderr or '')`
    — الغياب يُعامل كإخراج فارغ بدل الانهيار (الأسطر المجاورة نفسها
    كانت تستخدم `error.stdout or ""` في مسار المهلة، و`upload_pipeline`
    يستخدم `(proc.stdout or "")`).
  - أنبوب الطبيب يُفكّ بترميز UTF-8 صراحةً (`encoding="utf-8",
    errors="replace"`) بدل ترميز الإعدادات المحلية: الطبيب يعيد
    تهيئة إخراجه إلى UTF-8 (4.49.28)، وترميز Windows المحلي cp1252
    كان سيشوّه التقرير العربي عند فكّه.
- لا تغيير في بوابة الحقوق (تبقى `strict`)، ولا تقسيم للملف الرئيسي،
  و`version.py` يبقى المصدر الوحيد للإصدار.
- لا تغيير في `.github/workflows/release.yml` هذه المرة — تشديد فحص
  الدخان من 4.49.28 ما زال معلّقاً كملف يدوي (انظر أدناه).

## الاختبارات الجديدة (1 + تشخيص)

- `tests/test_control_plane.py::test_readiness_check_survives_none_stdout_from_doctor`:
  يثبّت أن `run_readiness_check` ينجو من `stdout=None` (مع `stderr`
  نصاً: يرجع الرمز والتقرير) ومن `stdout=None` مع `stderr=None`
  (يرجع رمز 0 وإخراجاً فارغاً) دون رفع.
- `tests/test_console_encoding.py::test_preflight_exits_2_in_clean_copy_under_strict_cp1252`:
  رسالة التأكيد صارت تطبع مخرجات العملية الفرعية كاملةً
  (`stdout` و`stderr`) عند الفشل، حتى لا يُخفى التتبع الحقيقي في
  سجلّ CI مرة أخرى.

## التحقق (بعد الإصلاح)

| البيئة | النتيجة |
|---|---|
| `.venv` Python 3.12.3 (مجلد المشروع) | 800 ناجحاً، 2 متخطيان |
| Python 3.11.18 (`~/.py311-test-venv`) | 800 ناجحاً، 2 متخطيان، 1 تحذير (سابق: `google.api_core` حول نهاية دعم 3.11) |
| الاختبارات الجديدة | 1 جديد ناجح + تأكيد تشخيصي |
| `ruff check .` | نظيف |
| نسخة نظيفة `PYTHONIOENCODING=cp1252` | `--version` 0، و`--help` 0، و`--preflight` 2 مع `VIDEO_DIRECTORY` وبلا `UnicodeEncodeError` |

الاختباران المتخطيان هما اختبارا أذونات POSIX عند التشغيل كـ root
(متوقعان). لا يوجد مفسّر Python 3.14 محلياً؛ يحتاج تأكيد CI بعد الدفع
كالمعتاد. لم يُدفع شيء ولم يُوسم شيء — كل التغييرات غير مُثبَّتة
بانتظار التحقق والموافقة.

## ملف يدوي: ما يجب على المستخدم إضافته على GitHub

`.github/workflows/release.yml` — تشديد فحص الدخان من 4.49.28 (فحص
صريح لرموز خروج `--version` و`--help`) ما زال غير موجود على GitHub؛
الملف محلياً يحويه، ونسخة احتياطية في
`~/workspace/release-4.49.28.yml`. يجب تطبيقه على GitHub **قبل**
إنشاء وسم `v4.49.29`، وإلا عمل بناء المثبّت بخطوة الفحص القديمة:

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

## ملفات متغيرة

- `control_plane.py`: حماية `stdout`/`stderr` الغائبين + أنبوب UTF-8 صريح.
- `tests/test_control_plane.py`: اختبار انحدار `stdout=None` (جديد).
- `tests/test_console_encoding.py`: رسالة تشخيص كاملة عند فشل اختبار النسخة النظيفة.
- `tests/test_hardening_44920.py`: اختبار اتساق الإصدار إلى 4.49.29.
- `version.py` و`README.md` و`AGENTS.md` و`CHANGELOG.md`
  و`installer/facbock-yo.iss` و`docs/packaging.md`: رقم الإصدار 4.49.29.
- `docs/validation/FINAL_VALIDATION_4.49.29.md`: هذا الملف.
