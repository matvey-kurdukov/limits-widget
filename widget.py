"""Виджет лимитов подписки Claude и Codex (ChatGPT) поверх всех окон. Только Windows.

Claude: вход Claude Code на этом компьютере (~/.claude/.credentials.json) или долгий
токен claude setup-token (CLAUDE_CODE_OAUTH_TOKEN в переменной или в настройках VS Code).
Шлём крошечный запрос к Haiku и читаем лимиты из заголовков ответа: /api/oauth/usage
часто отвечает 429 даже на рабочий токен, а долгий токен не пускает совсем.
Codex: тот же эндпоинт, что /status, токен из ~/.codex/auth.json.
Токены читаются на каждом опросе, сам виджет их не обновляет: это делает Claude Code
(Codex), когда им пользуются. Токены уходят только в api.anthropic.com и chatgpt.com.

Фон под строкой - размытый снимок того, что под ней, плюс тонировка темы.
Окно исключено из захвата экрана, иначе снимок ловил бы сам виджет. Побочный
эффект: на скриншотах и записи экрана виджета не видно.

python widget.py --check - проверка без окна для установщика.
"""
import ctypes
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageTk

HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
CLAUDE_CREDS = CLAUDE_DIR / ".credentials.json"
CODEX_AUTH = HOME / ".codex" / "auth.json"
# Настройки VS Code: туда кладут долгий токен, если вошли через claude setup-token
VSCODE_SETTINGS = Path(os.environ.get("APPDATA", "")) / "Code" / "User" / "settings.json"
PROBE_MODEL = "claude-haiku-4-5"  # запрос ради заголовков: 1 токен ответа, самая дешёвая модель
SETTINGS = Path(__file__).with_name("settings.json")
CACHE = Path(__file__).with_name("cache.json")
STYLES = ("Полоски", "Цифры", "Столбики")
POLL_MS = 30_000  # как часто проверять, не пора ли спросить
# Идёт работа в Claude Code или Codex - спрашиваем раз в минуту, простаивают - раз в 10 минут.
# Работа = файл переписки менялся за последние ACTIVE_WINDOW секунд
INTERVAL_ACTIVE, INTERVAL_IDLE, ACTIVE_WINDOW = 60, 600, 300
CLAUDE_SESSIONS = CLAUDE_DIR / "projects"
CODEX_SESSIONS = HOME / ".codex" / "sessions"
BACKOFF_BASE, BACKOFF_MAX = 120, 3600  # после 429: 2, 4, 8 ... минут, потолок час
STALE = 20 * 60  # цифры старше 20 минут - название серым
BG_MS = 700  # как часто обновлять размытый фон
BLUR = 10
FONT = "Segoe UI"
LABELS = {"claude": "Claude", "codex": "ChatGPT"}
APPS = {"claude": "открой Claude Code", "codex": "открой Codex"}  # что делать, если токен протух


def keys():
    """Порядок на строке. Claude есть всегда, ChatGPT - если на компьютере есть вход в Codex."""
    return ("claude", "codex") if CODEX_AUTH.exists() else ("claude",)

THEMES = {
    "dark": {
        "name": "Тёмная", "tint": (22, 22, 26), "fg": "#f2f2f4", "dim": "#a3a3ab", "track": "#44444c",
        "green": "#4ec27a", "yellow": "#e0b341", "red": "#ef5b5b",
    },
    "light": {
        "name": "Светлая", "tint": (246, 246, 248), "fg": "#141417", "dim": "#55555e", "track": "#c4c4ca",
        "green": "#138a43", "yellow": "#a86f00", "red": "#cc2b2b",
    },
}
OPACITY_STEPS = (0, 20, 40, 60, 80, 100)


def http_json(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": "codex-cli", **headers})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


class NoLogin(Exception):
    """Входа нет или он истёк: спрашивать API бесполезно, ждём, пока Claude Code его обновит."""


def claude_token():
    """Долгий токен setup-token из переменной или настроек VS Code; нет его - вход Claude Code из .credentials.json.
    Настройки VS Code могут быть с комментариями, поэтому не json.loads, а поиск по тексту."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    try:
        text = VSCODE_SETTINGS.read_text(encoding="utf-8-sig")
        m = re.search(r'"name"\s*:\s*"CLAUDE_CODE_OAUTH_TOKEN"\s*,\s*"value"\s*:\s*"([^"]+)"', text)
        if m:
            return m.group(1)
    except OSError:
        pass
    try:
        oauth = json.loads(CLAUDE_CREDS.read_text(encoding="utf-8"))["claudeAiOauth"]
    except (OSError, ValueError, KeyError, TypeError):
        raise NoLogin("нет входа, открой Claude Code") from None
    # Claude Code обновляет вход сам, когда им пользуются; истёк - значит давно не запускали
    if oauth.get("expiresAt") and oauth["expiresAt"] / 1000 < time.time():
        raise NoLogin("вход истёк, открой Claude Code")
    return oauth["accessToken"]


def fetch_claude():
    body = {
        "model": PROBE_MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}],
        "system": "You are Claude Code, Anthropic's official CLI for Claude.",
    }
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {claude_token()}", "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20", "content-type": "application/json", "User-Agent": "claude-cli",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            h = r.headers
    except urllib.error.HTTPError as ex:
        # Лимит выбран - API отвечает 429, но цифры в заголовках всё равно приходят
        if ex.headers.get("anthropic-ratelimit-unified-5h-utilization") is None:
            raise
        h = ex.headers

    def row(label, w):
        p = float(h.get(f"anthropic-ratelimit-unified-{w}-utilization") or 0) * 100
        ts = h.get(f"anthropic-ratelimit-unified-{w}-reset")
        return label, p, datetime.fromtimestamp(int(ts), timezone.utc) if ts else None

    return "Claude", [row("5ч", "5h"), row("нед", "7d")]


def window_label(sec):
    """У платного Codex окна 5 часов и неделя, у бесплатного одно на 30 дней."""
    h = (sec or 0) // 3600
    return "мес" if h >= 24 * 28 else "нед" if h >= 24 * 7 else f"{h}ч"


def fetch_codex():
    tokens = json.loads(CODEX_AUTH.read_text(encoding="utf-8"))["tokens"]
    d = http_json(
        "https://chatgpt.com/backend-api/wham/usage",
        {"Authorization": f"Bearer {tokens['access_token']}", "ChatGPT-Account-Id": tokens["account_id"]},
    )
    rl = d.get("rate_limit") or {}

    def row(w):
        ts = w.get("reset_at")
        return window_label(w.get("limit_window_seconds")), w.get("used_percent") or 0, datetime.fromtimestamp(ts, timezone.utc) if ts else None

    plan = (d.get("plan_type") or "").capitalize()
    return f"Codex {plan}".strip(), [row(w) for w in (rl.get("primary_window"), rl.get("secondary_window")) if w]


FETCH = {"claude": fetch_claude, "codex": fetch_codex}


def last_activity(key):
    """Когда последний раз писалась переписка: Claude Code - любой .jsonl в projects, Codex - за сегодня и вчера."""
    if key == "claude":
        files = CLAUDE_SESSIONS.rglob("*.jsonl")
    else:
        days = {datetime.now().astimezone(), datetime.fromtimestamp(time.time() - 86400).astimezone()}
        files = (f for d in days for f in (CODEX_SESSIONS / d.strftime("%Y/%m/%d")).glob("*.jsonl"))
    newest = 0
    for f in files:
        try:
            newest = max(newest, f.stat().st_mtime)
        except OSError:
            pass
    return newest


def reset_text(dt):
    if not dt:
        return ""
    left = int((dt - datetime.now(timezone.utc)).total_seconds())
    if left <= 0:
        return ""
    h, m = divmod(left // 60, 60)
    if h >= 24:
        return dt.astimezone().strftime("%d.%m")
    return f"{h}ч{m:02d}м" if h else f"{m}м"


class _BMI(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPels", ctypes.c_int32), ("biYPels", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
    ]


def grab_region(x, y, w, h):
    """Снимок только прямоугольника экрана. ImageGrab из Pillow снимает весь экран - в разы дороже."""
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    g.CreateCompatibleBitmap.restype = g.CreateCompatibleDC.restype = u.GetDC.restype = ctypes.c_void_p
    g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p] + [ctypes.c_int] * 2 + [ctypes.c_uint32]
    g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    g.DeleteObject.argtypes = g.DeleteDC.argtypes = [ctypes.c_void_p]
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h)
    try:
        g.SelectObject(mdc, bmp)
        if not g.BitBlt(mdc, 0, 0, w, h, sdc, x, y, 0x00CC0020):  # SRCCOPY
            raise OSError("BitBlt")
        bmi = _BMI(ctypes.sizeof(_BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(None, sdc)


def work_area():
    """Рабочая область экрана без панели задач: (left, top, right, bottom)."""
    r = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


class Widget:
    def __init__(self):
        # Координаты и шрифты в физических пикселях, иначе при масштабе 125% всё мыльное
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        self.root = tk.Tk()
        self.k = self.root.winfo_fpixels("1i") / 96
        self.H = self.px(22)
        self.S = self.px(28)  # сторона свёрнутого квадратика
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.root, width=10, height=self.H, highlightthickness=0)
        self.canvas.pack()
        # good - последние удачные цифры, переживают ошибки и перезапуск; data - что рисуем сейчас
        self.good, self.ok_at = self.load_cache()
        self.data = dict(self.good)
        self.updated = None
        self.last_try = {}  # key -> time.time() последнего запроса
        self.blocked_until = {}  # key -> time.time(), после 429 раньше не спрашиваем
        self.active = {}  # key -> идёт ли сейчас работа, для меню
        self.fails = {}  # key -> сколько 429 подряд, от этого растёт пауза
        self.errors = {}  # key -> последняя ошибка, видна в меню
        self.fetching = threading.Lock()
        self.bg_photo = None
        self.bg_item = None
        self.bg_key = None

        st = self.st = self.load_settings()
        self.style = tk.IntVar(value=st["style"])
        self.theme = tk.StringVar(value=st["theme"] if st["theme"] in THEMES else "dark")
        self.opacity = tk.IntVar(value=st["opacity"])
        self.mini = bool(st["mini"])
        self.btn_x = None  # где на строке кнопка «свернуть», в координатах холста
        self.press_xy = (-10_000, -10_000)
        self.toggled_at = 0
        self.skip_release = False
        self.root.geometry("+%d+%d" % self.pos())
        # Только на окне: события холста доходят до него сами, вторая привязка на холст вызывала всё дважды
        self.root.bind("<ButtonPress-1>", self.start_drag)
        self.root.bind("<B1-Motion>", self.drag)
        self.root.bind("<ButtonRelease-1>", self.release)
        self.root.bind("<Double-Button-1>", self.double)
        self.root.bind("<Button-3>", self.menu)
        self.root.bind("<MouseWheel>", self.wheel)

        self.root.update_idletasks()
        # LIMITS_WIDGET_DEBUG=1: без исключения из захвата, окно прячется на время снимка фона.
        # Мигает, зато виджет виден на скриншотах - только для проверки
        self.debug = bool(os.environ.get("LIMITS_WIDGET_DEBUG"))
        self.can_blur = self.debug or self.exclude_from_capture()
        self.set_corners()
        self.menu_open = False
        self.poll()
        self.tick()
        self.bg_loop()
        self.keep_on_top()

    @property
    def t(self):
        return THEMES[self.theme.get()]

    def color(self, p):
        t = self.t
        return t["red"] if p >= 85 else t["yellow"] if p >= 60 else t["green"]

    def px(self, n):
        return round(n * self.k)

    def exclude_from_capture(self):
        """WDA_EXCLUDEFROMCAPTURE: снимок фона не видит сам виджет. Нет поддержки - фон сплошной."""
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(self.canvas.winfo_id(), 2)  # GA_ROOT
        return bool(user32.SetWindowDisplayAffinity(hwnd, 0x11))

    def set_corners(self):
        """Квадратик скругляет сама Windows 11: края сглажены, у SetWindowRgn были бы лесенкой."""
        hwnd = ctypes.windll.user32.GetAncestor(self.canvas.winfo_id(), 2)
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        pref = ctypes.c_int(2 if self.mini else 1)  # DWMWCP_ROUND / DWMWCP_DONOTROUND
        dwm(hwnd, 33, ctypes.byref(pref), 4)
        no_border = ctypes.c_uint(0xFFFFFFFE)  # DWMWA_COLOR_NONE, без светлой рамки вокруг
        dwm(hwnd, 34, ctypes.byref(no_border), 4)

    # настройки и кэш
    def load_settings(self):
        left, _, right, bottom = work_area()
        gap = self.px(8)
        st = {
            "x": left + self.px(300), "y": bottom - self.H - self.px(3), "style": 0, "theme": "dark", "opacity": 60,
            "mini": False, "mini_x": right - self.S - gap, "mini_y": bottom - self.S - gap,
        }
        try:
            st.update(json.loads(SETTINGS.read_text(encoding="utf-8-sig")))
        except Exception:
            pass
        # Сохранённая позиция могла уехать за край: другой экран, масштаб, неудачное перетаскивание
        m = ctypes.windll.user32.GetSystemMetrics
        vx, vy, vw, vh = m(76), m(77), m(78), m(79)  # виртуальный экран, все мониторы

        def clamp(x, y, w, h):
            return max(vx, min(x, vx + vw - w)), max(vy, min(y, vy + vh - h))

        st["x"], st["y"] = clamp(st["x"], st["y"], self.px(100), self.H)
        st["mini_x"], st["mini_y"] = clamp(st["mini_x"], st["mini_y"], self.S, self.S)
        return st

    def pos(self):
        return (self.st["mini_x"], self.st["mini_y"]) if self.mini else (self.st["x"], self.st["y"])

    def save_settings(self, keep_pos=False):
        # Позиции строки и квадратика храним раздельно: свернул-развернул - каждый на своём месте
        if not keep_pos:
            kx, ky = ("mini_x", "mini_y") if self.mini else ("x", "y")
            self.st[kx], self.st[ky] = self.root.winfo_x(), self.root.winfo_y()
        self.st.update(style=self.style.get(), theme=self.theme.get(), opacity=self.opacity.get(), mini=self.mini)
        try:
            SETTINGS.write_text(json.dumps(self.st), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def load_cache():
        """Кэш: {key: [title, rows, ok_at]}."""
        good, ok_at = {}, {}
        try:
            raw = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            return good, ok_at
        for k, v in raw.items():
            try:
                t, rows = v[0], v[1]
                good[k] = (t, [(l, p, datetime.fromisoformat(r) if r else None) for l, p, r in rows])
                ok_at[k] = v[2]
            except Exception:
                continue
        return good, ok_at

    def save_cache(self):
        # Пишем только удачные цифры: ошибка не должна стирать то, что уже было
        good = {
            k: (t, [(l, p, r.isoformat() if r else None) for l, p, r in rows], self.ok_at.get(k, 0))
            for k, (t, rows) in self.good.items()
        }
        try:
            CACHE.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # мышь и меню
    def start_drag(self, e):
        self.dx, self.dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self.press_xy = (e.x_root, e.y_root)
        self.skip_release = False

    def release(self, e):
        if self.skip_release:  # отпустили кнопку после двойного клика, он уже всё сделал
            self.skip_release = False
            return
        if abs(e.x_root - self.press_xy[0]) + abs(e.y_root - self.press_xy[1]) <= self.px(3):
            cx = e.x_root - self.canvas.winfo_rootx()
            # Клик без перетаскивания: по квадратику - развернуть, по чёрточке на строке - свернуть
            if self.mini or (self.btn_x and self.btn_x[0] <= cx <= self.btn_x[1]):
                self.toggle()
                return
        self.save_settings()

    def double(self, e):
        self.skip_release = True
        if time.time() - self.toggled_at > 0.6:  # иначе первый клик пары уже развернул квадратик
            self.toggle()

    def toggle(self):
        """Строка <-> квадратик в углу."""
        self.save_settings()
        self.mini = not self.mini
        self.toggled_at = time.time()
        self.root.geometry("+%d+%d" % self.pos())
        self.set_corners()
        self.draw()
        self.save_settings(keep_pos=True)

    def drag(self, e):
        self.root.geometry(f"+{e.x_root - self.dx}+{e.y_root - self.dy}")
        self.refresh_bg()

    def wheel(self, e):
        step = 10 if e.delta > 0 else -10
        self.opacity.set(max(0, min(100, self.opacity.get() + step)))
        self.restyle()

    def menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        for key in keys():
            ok = self.ok_at.get(key)
            status = LABELS[key] + ": " + (datetime.fromtimestamp(ok).strftime("цифры от %d.%m %H:%M") if ok else "цифр нет")
            if key in self.active:
                status += ", " + ("работаешь - раз в минуту" if self.active[key] else "простой - раз в 10 минут")
            if key in self.errors:
                status += " - " + self.errors[key]
            m.add_command(label=status, state="disabled")
        m.add_command(label="Обновить", command=self.poll_now)
        m.add_command(label="Развернуть" if self.mini else "Свернуть в угол (двойной клик)", command=self.toggle)
        m.add_separator()
        view = tk.Menu(m, tearoff=0)
        for i, name in enumerate(STYLES):
            view.add_radiobutton(label=name, variable=self.style, value=i, command=self.restyle)
        m.add_cascade(label="Вид", menu=view)
        theme = tk.Menu(m, tearoff=0)
        for key, t in THEMES.items():
            theme.add_radiobutton(label=t["name"], variable=self.theme, value=key, command=self.restyle)
        m.add_cascade(label="Тема", menu=theme)
        opac = tk.Menu(m, tearoff=0)
        for v in OPACITY_STEPS:
            label = f"{v}%" + (" - только размытие" if v == 0 else " - сплошной" if v == 100 else "")
            opac.add_radiobutton(label=label, variable=self.opacity, value=v, command=self.restyle)
        m.add_cascade(label="Непрозрачность (колесо мыши)", menu=opac)
        m.add_separator()
        m.add_command(label="Закрыть", command=self.root.destroy)
        self.menu_open = True
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
            self.menu_open = False

    def restyle(self):
        self.save_settings()
        self.draw()

    # данные
    def poll_now(self, manual=True):
        if manual:  # «Обновить» из меню: не ждём обычной паузы, но после 429 всё равно выжидаем
            self.last_try.clear()
        threading.Thread(target=self.fetch_all, daemon=True).start()

    def poll(self):
        self.poll_now(manual=False)
        self.root.after(POLL_MS, self.poll)

    def fetch_all(self):
        if not self.fetching.acquire(blocking=False):  # прошлый опрос ещё идёт - не дублируем запрос
            return
        try:
            for key in keys():
                self.fetch_one(key, FETCH[key])
            self.updated = datetime.now()
            self.save_cache()
        finally:
            self.fetching.release()
        self.root.after(0, self.draw)

    def fetch_one(self, key, fn):
        now = time.time()
        if now < self.blocked_until.get(key, 0):
            return
        # Паузу считаем заново на каждом опросе: начал работать - через полминуты уже частый режим
        active = self.active[key] = now - last_activity(key) < ACTIVE_WINDOW
        interval = INTERVAL_ACTIVE if active else INTERVAL_IDLE
        if now - self.last_try.get(key, 0) < interval - 5:  # 5 с запаса на дрожание таймера
            return
        self.last_try[key] = now
        name = APPS[key]
        try:
            self.data[key] = self.good[key] = fn()
            self.ok_at[key] = now
            self.fails[key] = 0
            self.errors.pop(key, None)
            return
        except urllib.error.HTTPError as ex:
            if ex.code == 429:
                # Пауза растёт: 2, 4, 8, 16 минут, потолок час. Retry-After уважаем, если он дольше
                n = self.fails[key] = self.fails.get(key, 0) + 1
                try:
                    retry = int(ex.headers.get("Retry-After") or 0)
                except ValueError:
                    retry = 0
                self.blocked_until[key] = now + min(BACKOFF_MAX, max(retry, BACKOFF_BASE * 2 ** (n - 1)))
                err = "жду, API занят"
            elif ex.code == 401:
                err = f"токен протух, {name}"
            elif ex.code == 403:
                try:
                    blocked = b"not allowed for this organization" in ex.read()
                except Exception:
                    blocked = False
                err = "отключён от Claude Code" if blocked else "нет доступа (403)"
            else:
                err = f"ошибка {ex.code}"
            if ex.code in (401, 403):
                # Токен или аккаунт не пускают - старые цифры только обманут, убираем и из кэша
                self.good.pop(key, None)
                self.ok_at.pop(key, None)
        except NoLogin as ex:
            err = str(ex)
        except Exception as ex:
            err = f"нет связи: {type(ex).__name__}"
        # Есть прошлые цифры - показываем их, название станет серым, когда устареют
        self.data[key] = self.good[key] if key in self.good else (LABELS[key], err)
        self.errors[key] = err

    def has_rows(self, key):
        return key in self.data and not isinstance(self.data[key][1], str)

    @staticmethod
    def live(p, reset):
        """Окно лимита уже сбросилось, а свежих цифр нет - значит там 0."""
        return 0 if reset and reset <= datetime.now(timezone.utc) else round(p)

    def stale(self, key):
        return time.time() - self.ok_at.get(key, 0) > STALE

    def worst(self):
        """Самый загруженный из лимитов: он первым и упрётся. None - цифр ещё нет."""
        vals = [self.live(p, r) for k in keys() if self.has_rows(k) for _, p, r in self.data[k][1]]
        return max(vals) if vals else None

    def tick(self):
        self.draw()
        self.root.after(30_000, self.tick)

    def keep_on_top(self):
        """Topmost один раз мало: панель задач и другие окна «поверх всех» ложатся выше, когда их активируют.
        Поднимаемся сами, без фокуса. Пока открыто своё меню - не лезем, иначе накроем его."""
        if not self.menu_open:
            hwnd = ctypes.windll.user32.GetAncestor(self.canvas.winfo_id(), 2)
            # HWND_TOPMOST; SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
            ctypes.windll.user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x200)
        self.root.after(500, self.keep_on_top)

    # фон: размытый снимок под окном + тонировка темы
    def bg_loop(self):
        self.refresh_bg()
        self.root.after(BG_MS, self.bg_loop)

    def refresh_bg(self):
        if self.bg_item is None:
            return
        w, h, tint = self.canvas.winfo_reqwidth(), self.canvas.winfo_reqheight(), self.t["tint"]
        alpha = self.opacity.get() / 100
        if self.can_blur and alpha < 1:
            x, y, m = self.root.winfo_rootx(), self.root.winfo_rooty(), self.px(BLUR * 2)
            try:
                if self.debug:
                    self.root.attributes("-alpha", 0)
                    self.root.update()
                shot = grab_region(x - m, y - m, w + 2 * m, h + 2 * m)
            except OSError:  # экран заблокирован и т.п.
                return
            finally:
                if self.debug:
                    self.root.attributes("-alpha", 1)
            # Под виджетом ничего не поменялось - не пересчитываем, это почти весь CPU
            key = (shot.tobytes(), w, tint, alpha)
            if key == self.bg_key:
                return
            self.bg_key = key
            # Размываем уменьшенную вчетверо копию: результат тот же, работы в 16 раз меньше
            small = shot.resize((shot.width // 4, shot.height // 4), Image.BILINEAR)
            small = small.filter(ImageFilter.GaussianBlur(self.px(BLUR) / 4))
            img = small.resize(shot.size, Image.BILINEAR).crop((m, m, m + w, m + h))
            img = Image.blend(img, Image.new("RGB", img.size, tint), alpha)
        else:
            img = Image.new("RGB", (w, h), tint)
        if self.mini:
            img = self.ring(img)
        self.bg_photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfig(self.bg_item, image=self.bg_photo)

    def ring(self, img):
        """Кольцо квадратика. Рисуем вчетверо крупнее и уменьшаем: дуги Tk без сглаживания."""
        pct, k = self.worst(), 4
        big = Image.new("RGBA", (img.width * k, img.height * k), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        pad, lw = self.px(4) * k, self.px(2.4) * k
        box = (pad, pad, big.width - pad - 1, big.height - pad - 1)
        d.ellipse(box, outline=self.t["track"], width=lw)
        if pct:
            d.arc(box, -90, -90 + 360 * min(pct, 100) / 100, fill=self.color(pct), width=lw)
        out = img.convert("RGBA")
        out.alpha_composite(big.resize(img.size, Image.LANCZOS))
        return out.convert("RGB")

    # отрисовка
    # 0 Полоски:  Claude 5ч ▬ 42% 4ч18м  нед ▬ 27% 01.10 · Codex …
    # 1 Цифры:    Claude 42 · 27   Codex 0 · 87   (сброс 5ч мелко)
    # 2 Столбики: Claude ═ 42/27 - две полоски друг над другом, 5ч сверху
    def draw(self):
        c, mid, style, t = self.canvas, self.H // 2, self.style.get(), self.t
        c.delete("all")
        c.configure(bg="#%02x%02x%02x" % t["tint"])
        self.bg_item = c.create_image(0, 0, anchor="nw")
        self.bg_key = None  # новый пустой item - фон надо положить заново
        if self.mini:
            self.draw_mini()
            return
        x, px = self.px(8), self.px

        def text(s, fill, bold=False, gap=4, size=9):
            nonlocal x
            if not s:
                x += px(gap)
                return
            item = c.create_text(x, mid, text=s, anchor="w", fill=fill, font=(FONT, size, "bold" if bold else "normal"))
            x = c.bbox(item)[2] + px(gap)

        def bar(pct, y0, w=24, h=4):
            y0, w, h = mid + px(y0), px(w), px(h)
            c.create_rectangle(x, y0, x + w, y0 + h, fill=t["track"], width=0)
            c.create_rectangle(x, y0, x + w * min(pct, 100) / 100, y0 + h, fill=self.color(pct), width=0)

        shown = [k for k in keys() if k in self.data]
        if not shown:
            text("лимиты: загружаю…", t["dim"])
        for i, key in enumerate(shown):
            if i:
                text("·" if style == 0 else "", t["dim"], gap=10 if style == 0 else 20)
            title, rows = self.data[key]
            if isinstance(rows, str):
                text(LABELS[key], t["fg"], bold=True, gap=8)
                text(rows, t["dim"] if rows.startswith("жду") else t["red"], gap=8)
                continue
            text(LABELS[key], t["dim"] if self.stale(key) else t["fg"], bold=True, gap=8)
            # Окон обычно два (5ч и неделя), у бесплатного Codex одно (месяц)
            rows = [(l, self.live(p, r), r) for l, p, r in rows]
            if style == 0:
                for label, pct, reset in rows:
                    text(label, t["dim"])
                    bar(pct, -2)
                    x += px(29)
                    text(f"{pct}%", self.color(pct), bold=True, gap=3)
                    text(reset_text(reset) if pct else "", t["dim"], gap=10)
            elif style == 1:
                for i, (_, pct, _) in enumerate(rows):
                    if i:
                        text("·", t["dim"], gap=3)
                    text(str(pct), self.color(pct), bold=True, gap=4 if i == len(rows) - 1 else 3)
                text(reset_text(rows[0][2]) if rows and rows[0][1] else "", t["dim"], size=8, gap=6)
            else:
                for (_, pct, _), y0 in zip(rows, (-5, 2) if len(rows) > 1 else (-2,)):
                    bar(pct, y0, w=40, h=3)
                x += px(46)
                text("/".join(str(p) for _, p, _ in rows), t["fg"], gap=6, size=8)
        # Чёрточка «свернуть» в конце строки, зона клика шире самой линии
        x += px(2)
        c.create_line(x, mid, x + px(8), mid, fill=t["dim"], width=px(1.5))
        self.btn_x = (x - px(6), x + px(14))
        x += px(16)
        c.configure(width=x, height=self.H)
        c.tag_lower(self.bg_item)
        self.refresh_bg()

    def draw_mini(self):
        """Квадратик: кольцо и число самого загруженного лимита, кольцо дорисует refresh_bg."""
        c, s, pct = self.canvas, self.S, self.worst()
        self.btn_x = None
        fill = self.t["dim"] if pct is None else self.color(pct)
        c.create_text(s // 2, s // 2, text="…" if pct is None else str(pct), fill=fill, font=(FONT, 7, "bold"))
        c.configure(width=s, height=s)
        c.tag_lower(self.bg_item)
        self.refresh_bg()


def check():
    """Проверка без окна, для установщика: строка на аккаунт латиницей. Код выхода 1 - Claude не ответил."""
    ok = True
    for key in keys():
        try:
            _, rows = FETCH[key]()
            print(f"{LABELS[key]}: OK " + " / ".join(f"{round(p)}%" for _, p, _ in rows))
        except Exception as ex:
            ok = ok and key != "claude"
            code = getattr(ex, "code", "")
            hint = {401: ": token rejected, log in again with /login", 403: ": account has no access, needs Pro or Max"}
            why = "no login or it expired" if isinstance(ex, NoLogin) else f"{type(ex).__name__} {code}{hint.get(code, '')}"
            print(f"{LABELS[key]}: FAIL {why}".rstrip())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        Widget().root.mainloop()
