# التحقق النهائي — الإصدار 4.49.27

## الخلاصة

إصلاح فشل CI على Windows فقط في 4.49.26 دون أي تغيير في كود الإنتاج.
التشغيل 44326879878 نجح على Ubuntu (3/3) وفشل على Windows (3/3) عند
خطوة الاختبارات، بينما نجحت المجموعة محلياً وفي نسخة نظيفة. السبب
4 اختبارات مسارات مجمّدة تعتمد على POSIX، وأُعيد إنتاجها وتأكيد إصلاحها
بمحاكاة Windows محلياً.

## السبب الجذري

أربعة اختبارات في `tests/test_frozen_paths.py` كانت تستخدم تجهيزات
POSIX فقط، بينما كود الإنتاج نفسه صحيح على Windows:

1. `test_app_directory_uses_executable_when_frozen` و
   `test_doctor_directory_uses_executable_when_frozen`: يضعان
   `sys.executable = "/tmp/bundle/..."` ويتوقعان `"/tmp/bundle"`. على
   Windows يحوّل `ntpath.abspath` المسار إلى `\tmp\bundle` (وعبر
   Win32 `GetFullPathName` إلى `C:\tmp\bundle`)، فيفشل التحقق.
2. `test_enable_automation_dir_uses_executable_when_frozen`: يتوقع
   `Path("/tmp/bundle")`، بينما `Path.resolve()` على Windows يضيف
   محرك الأقراص الحالي (`C:\tmp\bundle`) للمسار الجذري بلا محرك
   (عبر `nt._getfullpathname` / `_getfinalpathname`).
3. `test_readiness_check_runs_doctor_executable_when_frozen`: ينشئ
   `facbock-yo-doctor` بلا لاحقة، بينما الكود يبحث على Windows عن
   `facbock-yo-doctor.exe` فيرجع رمز 2.

## إعادة الإنتاج (قبل الإصلاح)

محاكاة Windows محلية (`/tmp/win_sim/test_win_repro.py`, حُذفت بعد
التحقق): استدعاء دوال الاختبار الحقيقية مع `os.path` مُرقَّعاً إلى
`ntpath`، و`os.name` إلى `"nt"` عبر وكيل لوحدة `control_plane` (ترقيع
`os.name` عالمياً يكسر `pathlib.Path` على POSIX)، ومحاكاة مصغّرة لـ
`WindowsPath` تطبّق دلالات `GetFullPathName`:

```
4 failed, 2 passed
FAILED test_app_directory_uses_executable_when_frozen
  assert '\\tmp\\bundle' == '/tmp/bundle'
FAILED test_doctor_directory_uses_executable_when_frozen
  assert '\\tmp\\bundle' == '/tmp/bundle'
FAILED test_enable_automation_dir_uses_executable_when_frozen
  assert WinPath('C:\\tmp\\bundle') == WinPath('/tmp/bundle')
FAILED test_readiness_check_runs_doctor_executable_when_frozen
  assert 2 == 0
```

واختبارا الضبط (فحص الجاهزية لملف مفقود، ومراجعة المحتوى المجمّدة)
نجحا تحت المحاكاة نفسها. ونسخة نظيفة من `git archive HEAD` على Linux:
784 ناجحاً — لا فشل آخر مختبئاً.

## الإصلاح (اختبارات فقط)

- الاختبارات الثلاثة للمسارات تستخدم الآن مسار منفّذ من `tmp_path`
  (صحيح على كل منصة) بدل `"/tmp/bundle"` الثابت.
- اختبار فحص الجاهزية ينشئ الملف باللاحقة الصحيحة حسب المنصة
  (`facbock-yo-doctor.exe` على Windows).
- 4 اختبارات محاكاة Windows جديدة في `tests/test_frozen_paths.py`
  تثبّت السلوك على أي منصة: حلّ مسار منفّذ بنمط Windows
  (`C:\bundle\...`) للملف الرئيسي و`doctor.py` و`enable_automation.py`
  (الأخير بمحاكاة مصغّرة لـ `WindowsPath` لأن إنشاءه الحقيقي مستحيل
  على POSIX)، واختيار لاحقة `.exe` في فحص الجاهزية.
- لا تغيير في كود الإنتاج؛ رقم الإصدار 4.49.27 (ملفات التوثيق فقط).

## التحقق (بعد الإصلاح)

| البيئة | النتيجة |
|---|---|
| `.venv` Python 3.12.3 (مجلد المشروع) | 788 ناجحاً، 2 متخطيان |
| نسخة نظيفة تحاكي CI (ملفات متعقَّبة فقط + `CI=true GITHUB_ACTIONS=true`) | 788 ناجحاً، 2 متخطيان |
| تشغيل غير root كمستخدم `nobody` على نسخة نظيفة | 790 ناجحاً، 0 متخطيان (اختبارا أذونات POSIX يعملان وينجحان) |
| Python 3.11.18 (`~/.py311-test-venv`) | 788 ناجحاً، 2 متخطيان، 1 تحذير (سابق: `google.api_core` حول نهاية دعم 3.11) |
| اختبارات محاكاة Windows الجديدة | 4 ناجحة |
| `ruff check .` / `compileall` / `pip check` | نظيف / ناجح / لا اعتماديات مكسورة |
| `--version` | v4.49.27 |

الاختباران المتخطيان هما اختبارا أذونات POSIX عند التشغيل كـ root
(متوقعان). لا يوجد مفسّر Python 3.14 محلياً؛ يحتاج تأكيد CI بعد الدفع
كالمعتاد — وهو بالضبط ما يتحقق منه هذا الإصلاح على Windows.

## ملفات متغيرة

- `version.py` و`fb_youtube_uploader_v49_pro.py` (الترويسة) و`README.md`
  و`AGENTS.md` و`CHANGELOG.md` و`installer/facbock-yo.iss` و`docs/packaging.md`
  (أمثلة الأوامر): رقم الإصدار 4.49.27.
- `tests/test_frozen_paths.py`: إصلاح 4 اختبارات + 4 اختبارات محاكاة
  Windows جديدة.
- `tests/test_hardening_44920.py`: اختبار اتساق الإصدار إلى 4.49.27.
- `docs/validation/FINAL_VALIDATION_4.49.27.md`: هذا الملف.
