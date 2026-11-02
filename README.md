# FB → YouTube Uploader Pro

أداة مكتبية مكتوبة بـPython لإدارة مراجعة مقاطع Facebook ونشرها على YouTube، مع فحص مسبق، ومتابعة سجلّ الحقوق، وإعادة المحاولة. إصدار التطبيق الحالي: **4.46.0**.

## المتطلبات

- Python 3.11 أو أحدث.
- Tkinter لتشغيل الواجهة الرسومية.
- ملف OAuth من Google باسم `client_secrets.json`، يوضع محلياً بجانب البرنامج.
- FFmpeg إذا كنت تستخدم معالجة الفيديو أو إضافة الشعار.

## التثبيت والتشغيل

على Windows:

```bat
setup.bat
run_gui.bat
```

على Linux:

```bash
./setup.sh
./run_gui.sh
```

ينشئ التثبيت `config.ini` من `config.ini.example` عند عدم وجوده. أضف إعداد OAuth محلياً، ثم نفّذ فحص الجاهزية قبل التشغيل الآلي:

```bash
./run_automation.sh --preflight
```

للتشغيل الآلي استخدم `./run_automation.sh` أو `run_automation.bat` على Windows. راجع الإعدادات والحقوق قبل السماح بأي نشر.

## الاختبارات وفحص الشيفرة

```bash
./run_tests.sh
```

على Windows شغّل `run_tests.bat`. ولتشغيل الفحوص بعد تثبيت متطلبات التطوير:

```bash
python -m ruff check *.py tests/
python -m pytest -q
```

## تنظيم المشروع

- `fb_youtube_uploader_v49_pro.py`: نقطة الدخول والواجهة والتدفق الرئيسي.
- ملفات Python الأخرى في الجذر: وحدات المصادقة، السلامة، المراجعة، التشخيص والتحكم.
- `tests/`: الاختبارات الحالية؛ `tests/fixtures/versions/` لقطات تاريخية تستخدمها اختبارات التوافق فقط.
- `docs/UPGRADE.md`: نقل الإعدادات والبيانات من تثبيت أقدم.
- `config.ini.example` و`rights_manifest.example.json`: قوالب آمنة للبدء.

## البيانات والأمان

لا ترفع `config.ini` أو ملفات OAuth والكوكيز أو مفاتيح AI أو سجلات التشغيل أو بيانات القنوات والفيديوهات. هذه الملفات مستثناة في `.gitignore`؛ لا تتجاوز الاستثناءات عند النشر. احتفظ بنسخة احتياطية محلية لبيانات التشغيل قبل أي ترقية.
