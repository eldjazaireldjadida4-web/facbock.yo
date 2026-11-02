from __future__ import annotations

import fb_youtube_uploader_v49_pro as app


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Widget:
    def __init__(self):
        self.options = {}

    def configure(self, **options):
        self.options.update(options)

    config = configure


class _Tree:
    def __init__(self, values):
        self.values = tuple(values)

    def get_children(self):
        return ("page",)

    def selection(self):
        return ("page",)

    def item(self, _item, option=None, **options):
        if "values" in options:
            self.values = tuple(options["values"])
        if option == "values":
            return self.values
        return {"values": self.values}


def _ui_with_pages(pages):
    ui = app.App.__new__(app.App)
    ui.pages = pages
    ui.facebook_probe_page_combo = _Widget()
    ui.facebook_probe_page_var = _Var()
    ui.facebook_probe_status_var = _Var()
    ui.facebook_probe_button = _Widget()
    ui._facebook_probe_running = False
    return ui


def test_probe_page_selector_shows_only_enabled_pages_and_keeps_urls_mapped():
    ui = _ui_with_pages([
        {"enabled": True, "name": "أخبار", "url": "https://www.facebook.com/news"},
        {"enabled": False, "name": "موقوفة", "url": "https://www.facebook.com/paused"},
    ])

    labels = app.App._refresh_facebook_probe_pages(ui)

    assert labels == ["أخبار — https://www.facebook.com/news"]
    assert ui.facebook_probe_page_combo.options["values"] == labels
    assert ui.facebook_probe_page_var.get() == labels[0]
    assert ui._facebook_probe_page_urls[labels[0]] == "https://www.facebook.com/news"
    assert ui.facebook_probe_button.options["state"] == "normal"


def test_probe_page_selector_disables_test_when_no_page_is_enabled():
    ui = _ui_with_pages([
        {"enabled": False, "name": "موقوفة", "url": "https://www.facebook.com/paused"},
    ])

    labels = app.App._refresh_facebook_probe_pages(ui)

    assert labels == []
    assert ui.facebook_probe_page_var.get() == ""
    assert "لا توجد صفحات مفعّلة" in ui.facebook_probe_status_var.get()
    assert ui.facebook_probe_button.options["state"] == "disabled"


def test_page_toggle_persists_disabled_and_enabled_state(monkeypatch):
    saved = []
    monkeypatch.setattr(app, "save_pages", lambda pages: saved.append(pages))
    ui = app.App.__new__(app.App)
    ui.ptree = _Tree(("✓", "https://www.facebook.com/news", "أخبار", 5, 30))
    ui._refresh_watch_tree = lambda: None

    app.App._page_toggle(ui, None)
    assert ui.ptree.values[0] == "—"
    assert saved[-1][0]["enabled"] is False

    app.App._page_toggle(ui, None)
    assert ui.ptree.values[0] == "✓"
    assert saved[-1][0]["enabled"] is True
