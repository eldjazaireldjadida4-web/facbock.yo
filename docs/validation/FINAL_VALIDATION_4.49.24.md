# التحقق النهائي — الإصدار 4.49.24

## الخلاصة

إصلاح فشل CI في 4.49.23 دون أي تغيير في كود الإنتاج. التشغيل
44239844878 فشل في الوظائف الست عند خطوة الاختبارات، بينما نجحت
المجموعة محلياً في كل البيئات. السبب اختبار واحد يعتمد على `config.ini`
المحيط، وأُعيد إنتاجه وتأكيد إصلاحه في نسخة نظيفة تحاكي CI.

## السبب الجذري

- منذ 4.49.23 يستدعي `_run_cli_preflight` دالة
  `validate_configuration(config)` على الإعدادات المحيطة.
- محلياً يوجد `config.ini` صالح في مجلد المشروع (ملف متجاهل من git،
  غير موجود في المستودع)، فيمرّ التحقق.
- في نسخة CI النظيفة لا يوجد الملف، فيعيد `read_config` إعدادات
  فارغة، فيرفض `validate_configuration` غياب `VIDEO_DIRECTORY` و
  `CLIENT_SECRETS_FILE`، ويرجع الفحص المسبق رمز 2.
- الاختبار الساقط
  `tests/test_rights_gate.py::test_cli_preflight_uses_fingerprinted_oauth_token`
  كان يحاكي كل الاعتماديات الأخرى (`load_pages` و
  `validate_publication_controls` و`validate_content_review_settings`)
  إلا التحقق من الإعدادات، فنجح محلياً وسقط على CI.
- رفض إعدادات غائبة سلوك إنتاجي مقصود، لا عطل؛ المشكلة في الاختبار وحده.

## إعادة الإنتاج (قبل الإصلاح)

نسخة نظيفة من الملفات المتعقَّبة فقط (`git archive HEAD`) + بيئة جديدة
بنيت بتثبيت `requirements.txt` + `requirements-dev.txt` كما في CI +
متغيرا `CI=true GITHUB_ACTIONS=true`:

```
1 failed, 730 passed, 2 skipped
FAILED tests/test_rights_gate.py::test_cli_preflight_uses_fingerprinted_oauth_token
  assert 2 == 0
  • SETTINGS.VIDEO_DIRECTORY مطلوب.
  • SETTINGS.CLIENT_SECRETS_FILE مطلوب.
```

مستبعدات: `pip freeze` متطابق بين `.venv` والبيئة الجديدة (لا إصدار
اعتماديات جديد هو السبب)؛ والفشل ليس خاصاً بنظام أو بإصدار Python
(الوظائف الست كلها).

## الإصلاح

- `tests/test_rights_gate.py`: الاختبار يحاكي `validate_configuration`
  ليعيد `[]`، مع تعليق يشرح الاعتماد على الإعدادات المحيطة.
- `tests/test_hardening_44923.py`: اختبار انحدار جديد
  `test_cli_preflight_empty_ambient_config_reports_required_settings`
  يثبّت السلوك الإنتاجي: إعدادات فارغة تُرفض برمز 2 مع رسالتَي
  `VIDEO_DIRECTORY` و`CLIENT_SECRETS_FILE`.
- لا تغيير في كود الإنتاج؛ رقم الإصدار 4.49.24 (ملفات التوثيق فقط).

## التحقق (بعد الإصلاح)

| البيئة | النتيجة |
|---|---|
| `.venv` Python 3.12.3 (مجلد المشروع) | 732 ناجحاً، 2 متخطيان |
| بيئة جديدة `~/.ci-repro2-venv` Python 3.12.3 (تثبيت جديد كما في CI) | 732 ناجحاً، 2 متخطيان |
| نسخة نظيفة تحاكي CI (ملفات متعقَّبة فقط + `CI=true GITHUB_ACTIONS=true`) | 732 ناجحاً، 2 متخطيان |
| Python 3.11.18 (`~/.py311-test-venv`) | 732 ناجحاً، 2 متخطيان |
| تشغيل غير root كمستخدم `nobody` على نسخة نظيفة | 732 ناجحاً، 2 متخطيان |
| `ruff check .` | نظيف |
| `python -m compileall -q .` | ناجح |
| `python -m pip check` | لا اعتماديات مكسورة |
| `pip-audit -r requirements-lock.txt` | لا ثغرات معروفة |
| `--version` | v4.49.24 |

الاختباران المتخطيان هما اختبارا أذونات POSIX عند التشغيل كـ root
(متوقعان). لا يوجد مفسّر Python 3.14 محلياً؛ يحتاج تأكيد CI بعد الدفع
كالمعتاد.

## ملفات متغيرة

- `version.py` و`fb_youtube_uploader_v49_pro.py` (الترويسة) و`README.md`
  و`AGENTS.md` و`CHANGELOG.md`: رقم الإصدار 4.49.24.
- `tests/test_rights_gate.py`: محاكاة `validate_configuration`.
- `tests/test_hardening_44923.py`: اختبار الانحدار الجديد.
- `tests/test_hardening_44920.py`: اختبار اتساق الإصدار إلى 4.49.24.
- `docs/validation/FINAL_VALIDATION_4.49.24.md`: هذا الملف.
