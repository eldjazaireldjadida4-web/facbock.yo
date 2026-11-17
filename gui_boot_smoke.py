"""اختبار إقلاع الواجهة الفعلي تحت Xvfb: بناء النافذة، إيقاف، إغلاق بلا عامل."""
import sys, time, threading
sys.argv = ["fb_youtube_uploader_v49_pro.py"]
import fb_youtube_uploader_v49_pro as app

result = {"built": False, "destroyed": False, "os_exit": False, "error": None}
real_exit = sys.exit

# نفتح النافذة ثم نغلقها عبر _on_close بعد ثانية، ونرصد ما يحدث
def boot():
    try:
        import tkinter as tk
        root = tk.Tk()
        gui = app.App(root)
        result["built"] = True
        # تأكيد اكتمال البناء
        root.update_idletasks()
        assert root.winfo_exists()
        # لا عامل يعمل: الإغلاق العادي يجب أن يمضي بلا os._exit
        root.after(800, gui._on_close)
        root.mainloop()
        result["destroyed"] = True
    except SystemExit:
        result["os_exit"] = True  # يجب ألا يحدث هنا (بلا عامل حي)
    except Exception as e:
        result["error"] = repr(e)

boot()
print("RESULT:", result)
assert result["built"] and result["destroyed"], f"GUI boot failed: {result}"
assert not result["os_exit"], "os._exit fired without busy worker — regression!"
print("GUI BOOT + CLEAN CLOSE: OK")
