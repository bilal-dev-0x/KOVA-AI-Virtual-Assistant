"""The new dashboard, step 2: design tokens and the sidebar. Checked on the
files themselves: prompt.txt's tokens, no glow, no network references, the eight
screens of docs/KOVA_PRODUCT_PLAN.md.
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "ui" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")
JS = (WEB / "app.js").read_text(encoding="utf-8")


def test_nothing_refers_to_the_internet():
    for text, name in ((HTML, "index.html"), (CSS, "style.css"), (JS, "app.js")):
        assert "http://" not in text and "https://" not in text, name
        assert "@import" not in text and "cdn." not in text, name
        for fetched in re.findall(r'url\(\s*["\']?([^)"\']+)', CSS):
            assert not fetched.startswith("//") and "://" not in fetched, fetched


def test_design_tokens_are_the_ones_prompt_txt_sets():
    tokens = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", CSS))
    expected = {"--bg": "#10141d", "--surface": "#191f2b", "--accent": "#26c6da", "--text": "#e6eaf0",
                "--text-2": "#8994a5", "--success": "#36c98f", "--warning": "#e8b45b", "--danger": "#ed6472"}
    for name, value in expected.items():
        assert tokens[name].strip() == value, name
    for spacing in ("--s1", "--s2", "--s3", "--s4", "--s5"):
        assert tokens[spacing].strip() in ("4px", "8px", "16px", "24px", "32px")
    assert "Segoe UI Variable" in tokens["--font"]   # a Windows program's own face, nothing downloaded


def test_no_glow_gradient_blur_or_shadow():
    # Bilal rejected the glowing look (7 Nov); prompt.txt: no glowing borders or random neon
    for banned in ("box-shadow", "text-shadow", "backdrop-filter", "linear-gradient", "radial-gradient",
                   "filter: blur", "drop-shadow"):
        assert banned not in CSS, banned


def test_no_all_caps_labels():
    assert "text-transform: uppercase" not in CSS


def test_the_rail_names_all_eight_screens_with_icons():
    pages = re.findall(r'data-page="(\w+)"', HTML)
    assert pages == ["home", "assistant", "apps", "files", "system", "activity", "memory", "settings"]
    for page in pages:
        assert f'id="page-{page}"' in HTML
    for symbol in set(re.findall(r'<use href="#(i-\w+)"', HTML)):
        assert f'<symbol id="{symbol}"' in HTML   # every icon used is defined here


def test_phase_c_screens_have_real_data_controls():
    assert "Not built yet." not in HTML
    for element in ("apps-search", "apps-list", "files-folder", "files-list",
                    "system-status", "system-processes", "activity-list"):
        assert f'id="{element}"' in HTML
    for method in ("api.apps", "api.files", "api.system", "api.activity"):
        assert method in JS


def test_phase_c_live_review_polish_rules_exist():
    assert "scrollbar-color: var(--surface-2) var(--bg)" in CSS
    assert "::-webkit-scrollbar-thumb" in CSS
    assert "function formatActivityTime" in JS
    assert 'return "Today " + clock' in JS
    assert 'return "Yesterday " + clock' in JS
    assert "#page-files.page.is-active, #page-activity.page.is-active { gap: var(--s2); }" in CSS


def test_the_companion_clip_is_local_and_stops_when_unseen():
    for asset in ("reaper_idle.mp4", "reaper_poster.jpg", "reaper_icon.png"):
        assert (WEB / "assets" / asset).stat().st_size > 1000
    assert 'src="assets/reaper_idle.mp4"' in HTML and "autoplay" not in HTML
    assert "visibilitychange" in JS and "video.pause()" in JS


def test_the_page_state_and_boot_handlers_exist():
    assert "receive: receive" in JS   # the one Python entry point
    assert "applyState(event)" in JS.split("handlers", 1)[0] or "applyState" in JS
    assert "status-dot" in JS and "status-text" in JS
    assert "tickClock" in JS and "toLocaleString" in JS   # the real date and time


def test_the_real_window_serves_the_page_over_http():
    """Served over the internal HTTP server so the companion clip and icons load."""
    import inspect

    from ui import web_app

    source = inspect.getsource(web_app.WebDashboard.mainloop)
    assert "http_server=True" in source
