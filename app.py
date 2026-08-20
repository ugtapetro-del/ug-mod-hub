from __future__ import annotations

import math
import os
import csv
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk

from core import (
    APP_DIR,
    DEFAULT_ACCOUNT_URL,
    DEFAULT_CATALOG_URL,
    DEFAULT_UPDATE_URL,
    DEFAULT_WEB_HEALTH_URL,
    RESOURCE_DIR,
    Mod,
    ModError,
    OperationCancelled,
    AccessoryTarget,
    SkinTarget,
    WeaponTarget,
    acquire_single_instance,
    add_custom_mod,
    autostart_enabled,
    check_application_update,
    check_web_hosting,
    clear_fastman_access,
    configure_fastman_access,
    cover_path,
    delete_custom_mod,
    detect_game_candidates,
    download_application_update,
    ensure_editable_payload,
    export_locked_release,
    install_mod,
    find_accessory_template,
    find_skin_template,
    img_contains_skin,
    installed_mod_entries,
    installed_file_conflicts,
    is_trusted_origin_url,
    is_admin,
    is_dev_mode,
    launch_game,
    launch_application_updater,
    login_account,
    load_catalog,
    load_favorites,
    load_history,
    load_mta_servers,
    load_settings,
    load_accessory_targets,
    load_skin_targets,
    load_weapon_targets,
    load_state,
    mod_payload_available,
    payload_dir,
    payload_files,
    payload_size,
    preflight_check,
    record_history,
    register_account,
    restore_clean_game,
    restart_as_admin,
    running_game_processes,
    release_single_instance,
    save_settings,
    load_account,
    logout_account,
    protect_local_secret,
    resend_account_email,
    set_autostart,
    set_favorite,
    sync_managed_resources,
    uninstall_mod,
    update_online_catalog,
    update_dev_mod,
    unprotect_local_secret,
    validate_game_root,
    verify_installed_files,
)


BG = "#07090d"
PANEL = "#10141b"
PANEL_2 = "#171d26"
LINE = "#28313d"
TEXT = "#f2f4f7"
MUTED = "#858d98"
ACCENT = "#ffad3f"
ACCENT_HOVER = "#ffc35d"
RED = "#f06b7d"
BLUE = "#66c7ff"
GREEN = "#57df83"
RAIL_BG = "#0a0d12"
CATEGORIES = ["Кров + звук влучання", "Небо", "Ефекти", "Анімації", "Звуки пострілів", "Приціл", "HUD", "Скіни", "Аксесуари", "Заміна зброї", "Інше"]
DEFAULT_SERVER_CHOICES = [
    {"name": "#01 Центральна Україна", "host": "s1.ukraine-gta.com.ua", "port": 22003},
    {"name": "#02 Західна Україна", "host": "s2.ukraine-gta.com.ua", "port": 22003},
    {"name": "#03 Східна Україна", "host": "s3.ukraine-gta.com.ua", "port": 22003},
    {"name": "#04 Північна Україна", "host": "s4.ukraine-gta.com.ua", "port": 22003},
    {"name": "#05 Південна Україна", "host": "s5.ukraine-gta.com.ua", "port": 22003},
    {"name": "#06 Подільський Край", "host": "s6.ukraine-gta.com.ua", "port": 22003},
    {"name": "#07 Чорноморський край", "host": "s7.ukraine-gta.com.ua", "port": 22003},
]
CATEGORY_COVER_FILES = {
    "Кров + звук влучання": "blood_hit.png",
    "Небо": "sky.png",
    "Ефекти": "effects.png",
    "Анімації": "animations.png",
    "Звуки пострілів": "gun_sounds.png",
    "Приціл": "crosshair.png",
    "HUD": "hud.png",
    "Скіни": "other.png",
    "Аксесуари": "other.png",
    "Заміна зброї": "other.png",
    "Інше": "other.png",
}

MOD_STATUS_ALIASES = {
    "paused": "paused",
    "suspended": "paused",
    "призупинено": "paused",
    "development": "development",
    "in_development": "development",
    "in-development": "development",
    "у розробці": "development",
}


def mod_catalog_status(mod: Mod) -> str:
    return MOD_STATUS_ALIASES.get(str(mod.status or "").strip().lower(), "available")


def enable_dpi_awareness():
    """Let Windows report real monitor sizes on scaled and mixed-DPI displays."""
    if os.name != "nt":
        return
    try:
        import ctypes

        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def blend(a: str, b: str, amount: float) -> str:
    ar, ag, ab = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    br, bg, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(x + (y - x) * amount) for x, y in zip((ar, ag, ab), (br, bg, bb)))
    return "#%02x%02x%02x" % rgb


def mod_matches_search(mod: Mod, query: str) -> bool:
    needle = query.strip().casefold()
    if not needle:
        return True
    searchable = " ".join((mod.title, mod.description, mod.category, mod.id)).casefold()
    return needle in searchable


def format_account_date(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return "дата не вказана"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d.%m.%Y о %H:%M")
    except ValueError:
        return value


def available_server_choices(game_root: str, current_host: str = "", current_port: int = 22003) -> list[dict]:
    """Return every known UKRAINE GTA server, enriched with the local MTA cache."""
    choices = {(item["host"], int(item["port"])): dict(item) for item in DEFAULT_SERVER_CHOICES}
    try:
        cached = load_mta_servers(game_root)
    except (ModError, OSError, ValueError):
        cached = []
    for item in cached:
        host = str(item.get("host", "")).strip()
        name = str(item.get("name") or host)
        try:
            port = int(item.get("port", 22003))
        except (TypeError, ValueError):
            continue
        official = "ukraine-gta" in host.casefold() or "ukraine gta" in name.casefold()
        if host and official:
            choices[(host, port)] = {"name": name, "host": host, "port": port}
    try:
        normalized_current_port = int(current_port)
    except (TypeError, ValueError):
        normalized_current_port = 22003
    if current_host:
        key = (str(current_host), normalized_current_port)
        choices.setdefault(key, {"name": "Останній вибраний сервер", "host": key[0], "port": key[1]})
    known_order = {(item["host"], int(item["port"])): index for index, item in enumerate(DEFAULT_SERVER_CHOICES)}
    return sorted(
        choices.values(),
        key=lambda item: (known_order.get((item["host"], int(item["port"])), 999), item["name"].casefold()),
    )


def rounded_rectangle(canvas: tk.Canvas, x1, y1, x2, y2, radius=18, **kwargs):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class StartupSplash:
    """Cinematic multi-layer loader shown while the signed catalog initializes."""

    BASE_WIDTH = 680
    BASE_HEIGHT = 440

    def __init__(self, app: "ModHub", on_finished):
        self.app = app
        self.on_finished = on_finished
        self.started = time.monotonic()
        self.ready = False
        self.phase = 0.0
        self.alpha = 1.0
        self.finished = False
        self.error_mode = False
        self.retry_callback = None
        self.operation_progress: float | None = None
        self.frames: list[ImageTk.PhotoImage] = []
        self.particles = []
        self.equalizer = []
        self.background_photo = None

        window = tk.Toplevel(app)
        window.overrideredirect(True)
        window.configure(bg="#03060a")
        window.attributes("-topmost", True)
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        scale = min(1.0, max(0.66, min((screen_width - 28) / self.BASE_WIDTH, (screen_height - 42) / self.BASE_HEIGHT)))
        width, height = round(self.BASE_WIDTH * scale), round(self.BASE_HEIGHT * scale)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

        canvas = tk.Canvas(window, width=width, height=height, bg="#03060a", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        self.background_photo = self._make_background(width, height)
        canvas.create_image(0, 0, image=self.background_photo, anchor="nw")
        rounded_rectangle(canvas, 2, 2, 678, 438, 30, fill="", outline="#314254", width=2)
        rounded_rectangle(canvas, 10, 10, 670, 430, 26, fill="", outline="#202a36", width=1)

        # Technical corner brackets and animated scanner.
        for points in (((25, 58), (25, 25), (58, 25)), ((622, 25), (655, 25), (655, 58)),
                       ((25, 382), (25, 415), (58, 415)), ((622, 415), (655, 415), (655, 382))):
            canvas.create_line(*sum(([x, y] for x, y in points), []), fill="#476884", width=2)
        self.scan_line = canvas.create_rectangle(34, 32, 646, 33, fill=BLUE, outline="", stipple="gray50")

        # Layered reactor around the app icon.
        self.glow_outer = canvas.create_oval(177, 39, 503, 365, fill="#0b121b", outline="")
        self.glow_inner = canvas.create_oval(220, 82, 460, 322, fill="#0b1017", outline="#26394b", width=1)
        self.ring_far = canvas.create_arc(184, 46, 496, 358, start=12, extent=122, style="arc", outline="#31485d", width=1)
        self.ring_soft = canvas.create_arc(196, 58, 484, 346, start=222, extent=74, style="arc", outline=BLUE, width=2)
        self.ring = canvas.create_arc(210, 72, 470, 332, start=90, extent=96, style="arc", outline=ACCENT, width=4)
        self.ring_inner = canvas.create_arc(231, 93, 449, 311, start=292, extent=54, style="arc", outline="#c6e9ff", width=2)
        self.orbit_nodes = [canvas.create_oval(0, 0, 7, 7, fill=ACCENT, outline="#ffe0aa") for _ in range(4)]
        self.logo_item = canvas.create_image(340, 199)

        mode = getattr(app, "animation_mode", "Повні")
        particle_count = 30 if mode == "Повні" else (12 if mode == "Спрощені" else 0)
        for index in range(particle_count):
            px = 35 + ((index * 83) % 610)
            py = 32 + ((index * 47) % 350)
            radius = 1 + (index % 3 == 0)
            item = canvas.create_oval(px - radius, py - radius, px + radius, py + radius,
                                      fill=BLUE if index % 3 else ACCENT, outline="")
            self.particles.append((item, px, py, .35 + (index % 5) * .11, index * .7))

        canvas.create_text(340, 334, text="UG MOD HUB", fill=TEXT, font=("Segoe UI Black", 25))
        canvas.create_text(340, 359, text="NIGHTLINE MOD ECOSYSTEM", fill="#71879a", font=("Consolas", 8))
        self.status_item = canvas.create_text(340, 382, text="ПІДГОТОВКА ЗАСТОСУНКУ", fill="#93a5b7", font=("Segoe UI Semibold", 9))
        # Regular rectangles keep the animated width mathematically stable.
        self.progress_track = canvas.create_rectangle(93, 403, 587, 409, fill="#202a35", outline="")
        self.progress_item = canvas.create_rectangle(93, 403, 96, 409, fill=ACCENT, outline="")
        self.progress_glow = canvas.create_oval(90, 399, 99, 413, fill="#ffd08a", outline="")
        self.percent_item = canvas.create_text(612, 406, text="00%", fill="#89a9c2", font=("Consolas", 8))
        for index in range(19):
            bar = canvas.create_rectangle(281 + index * 6, 420, 284 + index * 6, 422, fill="#273a4b", outline="")
            self.equalizer.append(bar)

        self.retry_box = rounded_rectangle(canvas, 190, 394, 330, 424, 10, fill="#123733", outline=ACCENT, width=1, state="hidden")
        self.retry_text = canvas.create_text(260, 409, text="ПОВТОРИТИ", fill=TEXT, font=("Segoe UI Semibold", 9), state="hidden")
        self.exit_box = rounded_rectangle(canvas, 350, 394, 490, 424, 10, fill="#26141a", outline="#7b3040", width=1, state="hidden")
        self.exit_text = canvas.create_text(420, 409, text="ВИЙТИ", fill="#ff9aae", font=("Segoe UI Semibold", 9), state="hidden")
        if scale != 1.0:
            canvas.scale("all", 0, 0, scale, scale)

        self.window, self.canvas, self.scale = window, canvas, scale
        for item in (self.retry_box, self.retry_text):
            canvas.tag_bind(item, "<Button-1>", lambda _event: self._retry())
            canvas.tag_bind(item, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
            canvas.tag_bind(item, "<Leave>", lambda _event: canvas.configure(cursor=""))
        for item in (self.exit_box, self.exit_text):
            canvas.tag_bind(item, "<Button-1>", lambda _event: self._exit())
            canvas.tag_bind(item, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
            canvas.tag_bind(item, "<Leave>", lambda _event: canvas.configure(cursor=""))
        window.bind("<Return>", lambda _event: self._retry() if self.error_mode else None)
        window.bind("<Escape>", lambda _event: self._exit() if self.error_mode else None)
        self._prepare_frames()
        self._animate()

    def _make_background(self, width, height):
        image = Image.new("RGB", (width, height), "#04080d")
        draw = ImageDraw.Draw(image, "RGBA")
        for y in range(height):
            ratio = y / max(1, height - 1)
            draw.line((0, y, width, y), fill=(4, round(10 + 6 * ratio), round(15 + 10 * ratio), 255))
        for radius in range(round(width * .54), 20, -12):
            alpha = max(1, round(8 * (1 - radius / (width * .54))))
            cx, cy = width * .5, height * .42
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(49, 139, 204, alpha))
        grid = max(22, round(34 * width / self.BASE_WIDTH))
        for x in range(0, width, grid):
            draw.line((x, 0, x, height), fill=(78, 126, 163, 18))
        for y in range(0, height, grid):
            draw.line((0, y, width, y), fill=(78, 126, 163, 16))
        draw.polygon(((0, height), (0, height * .76), (width * .5, height * .45), (width, height * .76), (width, height)), fill=(4, 8, 14, 145))
        return ImageTk.PhotoImage(image)

    def _prepare_frames(self):
        candidates = [
            RESOURCE_DIR / "assets" / "nightline" / "ug-logo.png",
            RESOURCE_DIR / "assets" / "ukraine_gta_app_icon.ico",
            RESOURCE_DIR / "assets" / "app_icon.png",
        ]
        source = next((path for path in candidates if path.is_file()), None)
        if source is None:
            return
        try:
            frame_side = max(122, round(184 * self.scale))
            logo = Image.open(source).convert("RGBA")
            logo.thumbnail((max(90, round(138 * self.scale)), max(90, round(138 * self.scale))), Image.Resampling.LANCZOS)
            for index in range(24):
                wave = (math.sin(index / 24 * math.tau) + 1) / 2
                size = max(82, round((118 + wave * 16) * self.scale))
                resized = logo.resize((size, size), Image.Resampling.LANCZOS)
                frame = Image.new("RGBA", (frame_side, frame_side), (0, 0, 0, 0))
                alpha = resized.getchannel("A")
                glow = Image.new("RGBA", resized.size, (68, 164, 229, 0))
                glow.putalpha(alpha.point(lambda value, strength=round(70 + wave * 55): value * strength // 255))
                glow = glow.filter(ImageFilter.GaussianBlur(max(7, round((12 + wave * 7) * self.scale))))
                offset = ((frame_side - size) // 2, (frame_side - size) // 2)
                frame.alpha_composite(glow, offset)
                frame.alpha_composite(resized, offset)
                self.frames.append(ImageTk.PhotoImage(frame))
        except (OSError, ValueError, tk.TclError):
            self.frames.clear()

    def set_status(self, text: str):
        try:
            self.canvas.itemconfigure(self.status_item, text=text.upper())
        except tk.TclError:
            pass

    def set_progress(self, current, total, filename, bytes_done=0, total_bytes=0):
        ratio = (bytes_done / total_bytes) if total_bytes else (current / max(total, 1))
        self.operation_progress = min(0.985, max(0.0, float(ratio)))
        short_name = Path(str(filename)).name
        self.set_status(f"КАТАЛОГ {current}/{total} · {short_name[:28]}")

    def mark_ready(self):
        if self.error_mode:
            return
        self.ready = True
        self.operation_progress = 1.0
        self.set_status("СИНХРОНІЗОВАНО • ГОТОВО")

    def mark_error(self, text: str, retry_callback):
        if self.finished:
            return
        self.ready = False
        self.error_mode = True
        self.retry_callback = retry_callback
        scale = self.scale
        try:
            self.canvas.itemconfigure(self.status_item, text=text.upper(), fill=RED)
            self.canvas.coords(self.status_item, 340 * scale, 373 * scale)
            self.canvas.itemconfigure(self.progress_track, state="hidden")
            self.canvas.itemconfigure(self.progress_item, state="hidden")
            self.canvas.itemconfigure(self.progress_glow, state="hidden")
            self.canvas.itemconfigure(self.percent_item, state="hidden")
            for bar in self.equalizer:
                self.canvas.itemconfigure(bar, state="hidden")
            for item in (self.retry_box, self.retry_text, self.exit_box, self.exit_text):
                self.canvas.itemconfigure(item, state="normal")
            self.window.attributes("-alpha", 1.0)
            self.window.lift()
            self.window.focus_force()
        except tk.TclError:
            pass

    def _retry(self):
        if not self.error_mode or self.retry_callback is None:
            return
        callback = self.retry_callback
        self.retry_callback = None
        self.error_mode = False
        self.ready = False
        self.operation_progress = None
        self.started = time.monotonic()
        scale = self.scale
        try:
            self.canvas.itemconfigure(self.status_item, text="ПЕРЕВІРКА ЗВ’ЯЗКУ З СЕРВЕРОМ", fill=MUTED)
            self.canvas.coords(self.status_item, 340 * scale, 382 * scale)
            self.canvas.itemconfigure(self.progress_track, state="normal")
            self.canvas.itemconfigure(self.progress_item, state="normal")
            self.canvas.itemconfigure(self.progress_glow, state="normal")
            self.canvas.itemconfigure(self.percent_item, state="normal")
            for bar in self.equalizer:
                self.canvas.itemconfigure(bar, state="normal")
            for item in (self.retry_box, self.retry_text, self.exit_box, self.exit_text):
                self.canvas.itemconfigure(item, state="hidden")
        except tk.TclError:
            return
        callback()

    def _exit(self):
        if self.finished:
            return
        self.finished = True
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.app.close_app()

    def _animate(self):
        if self.finished:
            return
        try:
            mode = getattr(self.app, "animation_mode", "Повні")
            speed = .095 if mode == "Повні" else (.035 if mode == "Спрощені" else 0.0)
            self.phase += speed
            angle = (self.phase * 145) % 360
            self.canvas.itemconfigure(self.ring, start=angle)
            self.canvas.itemconfigure(self.ring_soft, start=(-angle * .72 + 168) % 360)
            self.canvas.itemconfigure(self.ring_far, start=(angle * .38 + 18) % 360)
            self.canvas.itemconfigure(self.ring_inner, start=(-angle * 1.2 + 292) % 360)
            if self.frames:
                frame_index = int(self.phase * 9) % len(self.frames)
                self.canvas.itemconfigure(self.logo_item, image=self.frames[frame_index])
            pulse = (math.sin(self.phase * 2.1) + 1) / 2
            self.canvas.itemconfigure(self.glow_outer, fill=blend("#091019", "#132536", pulse * .72))
            self.canvas.itemconfigure(self.glow_inner, outline=blend("#26394b", "#6a4827", pulse * .7))
            scan_y = 34 + ((self.phase * 42) % 365)
            self.canvas.coords(self.scan_line, 34 * self.scale, scan_y * self.scale, 646 * self.scale, (scan_y + 1) * self.scale)
            for index, node in enumerate(self.orbit_nodes):
                theta = self.phase * (1.1 + index * .11) + index * math.tau / len(self.orbit_nodes)
                radius = 144 - (index % 2) * 25
                x = 340 + math.cos(theta) * radius
                y = 199 + math.sin(theta) * radius
                size = 3 + (index % 2)
                self.canvas.coords(node, (x-size)*self.scale, (y-size)*self.scale, (x+size)*self.scale, (y+size)*self.scale)
            for index, (item, px, py, particle_speed, offset) in enumerate(self.particles):
                y = 32 + ((py - 32 - self.phase * 18 * particle_speed) % 350)
                x = px + math.sin(self.phase * particle_speed + offset) * 9
                size = 1.2 + ((math.sin(self.phase * 2 + offset) + 1) * .45)
                self.canvas.coords(item, (x-size)*self.scale, (y-size)*self.scale, (x+size)*self.scale, (y+size)*self.scale)
            elapsed = time.monotonic() - self.started
            progress = min(0.94, elapsed / 2.4 * 0.82)
            if self.operation_progress is not None:
                progress = self.operation_progress
            if self.ready:
                progress = min(1.0, max(progress, .84 + max(0, elapsed - 1.55) * .62))
            scale = self.scale
            progress_x = 93 + 494 * progress
            self.canvas.coords(self.progress_item, 93*scale, 403*scale, progress_x*scale, 409*scale)
            self.canvas.coords(self.progress_glow, (progress_x-5)*scale, 398*scale, (progress_x+5)*scale, 414*scale)
            self.canvas.itemconfigure(self.percent_item, text=f"{round(progress * 100):02d}%")
            for index, bar in enumerate(self.equalizer):
                amplitude = 2 + (math.sin(self.phase * 3.2 + index * .72) + 1) * 4
                self.canvas.coords(bar, (281 + index*6)*scale, (422-amplitude)*scale, (284 + index*6)*scale, 422*scale)
                self.canvas.itemconfigure(bar, fill=blend("#273a4b", ACCENT, min(1, amplitude / 9)))
            if self.ready and elapsed >= 2.35:
                self._fade_out()
                return
            self.window.after(24 if mode == "Повні" else (65 if mode == "Спрощені" else 240), self._animate)
        except tk.TclError:
            return

    def _fade_out(self):
        if self.finished:
            return
        self.alpha -= 0.09
        if self.alpha > 0:
            try:
                self.window.attributes("-alpha", self.alpha)
                self.window.after(22, self._fade_out)
            except tk.TclError:
                pass
            return
        self.finished = True
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.on_finished()


class ScrollFrame(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=BG, **kwargs)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._sync_scroll)
        self.canvas.bind("<Configure>", self._sync_width)
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _sync_scroll(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass


class AnimatedAccentBar(tk.Canvas):
    def __init__(self, parent, app):
        super().__init__(parent, height=2, bg="#0a0d12", highlightthickness=0, bd=0)
        self.app = app
        self.phase = 0.0
        self.bind("<Configure>", lambda _event: self._draw())
        self.after(60, self._animate)

    def _draw(self):
        self.delete("all")
        width = max(1, self.winfo_width())
        blocks = max(24, min(90, width // 18))
        block_width = width / blocks
        for index in range(blocks):
            wave = (math.sin(index * .32 + self.phase) + 1) / 2
            color = blend("#17202a", BLUE if index % 5 else ACCENT, max(0, wave - .45) * 1.3)
            self.create_rectangle(index * block_width, 0, (index + 1) * block_width + 1, 2, fill=color, outline="")

    def _animate(self):
        try:
            if self.app.animation_mode == "Повні" and not self.app._window_minimized:
                self.phase += .22
                self._draw()
                delay = 55
            elif self.app.animation_mode == "Спрощені":
                self.phase += .08
                self._draw()
                delay = 180
            else:
                delay = 700
            self.after(delay, self._animate)
        except tk.TclError:
            pass


class AnimatedHero(tk.Canvas):
    def __init__(self, parent, app):
        super().__init__(parent, bg=PANEL, highlightthickness=0, bd=0)
        self.app = app
        self.phase = 0.0
        self.image = app.hero_image()
        self.image_item = self.create_image(0, 0, image=self.image, anchor="nw")
        self.beams = [self.create_line(0, 0, 0, 0, fill="#234967", width=1) for _ in range(14)]
        self.dots = [self.create_oval(0, 0, 3, 3, fill=BLUE if i % 3 else ACCENT, outline="") for i in range(24)]
        self.rings = [self.create_arc(0, 0, 0, 0, start=i * 74, extent=72 - i * 7, style="arc", outline=color, width=max(1, 3-i))
                      for i, color in enumerate((ACCENT, BLUE, "#526b85"))]
        self.core = self.create_oval(0, 0, 0, 0, fill="#101820", outline="#47718d", width=1)
        self.core_text = self.create_text(0, 0, text="UG", fill=BLUE, font=("Segoe UI Black", 20))
        self.bind("<Configure>", self._layout)
        self.after(40, self._animate)

    def _layout(self, _event=None):
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        cx, cy = width * .79, height * .5
        for index, ring in enumerate(self.rings):
            radius = 52 + index * 25
            self.coords(ring, cx-radius, cy-radius, cx+radius, cy+radius)
        self.coords(self.core, cx-37, cy-37, cx+37, cy+37)
        self.coords(self.core_text, cx, cy)

    def _animate(self):
        try:
            if not self.winfo_exists():
                return
            mode = self.app.animation_mode
            self.phase += .085 if mode == "Повні" else (.025 if mode == "Спрощені" else 0.0)
            width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
            cx, cy = width * .79, height * .5
            for index, ring in enumerate(self.rings):
                self.itemconfigure(ring, start=(self.phase * (95 - index * 23) * (-1 if index == 1 else 1) + index * 80) % 360)
            pulse = (math.sin(self.phase * 2.4) + 1) / 2
            radius = 35 + pulse * 5
            self.coords(self.core, cx-radius, cy-radius, cx+radius, cy+radius)
            self.itemconfigure(self.core, outline=blend("#31516b", ACCENT, pulse))
            for index, beam in enumerate(self.beams):
                x = ((index * 127 + self.phase * (54 + index * 2)) % (width + 180)) - 90
                y = 24 + ((index * 43) % max(40, height - 48))
                self.coords(beam, x-65, height+8, x+90, y)
                self.itemconfigure(beam, fill=blend("#152735", BLUE, (math.sin(self.phase + index) + 1) * .18))
            for index, dot in enumerate(self.dots):
                angle = self.phase * (.45 + (index % 4) * .08) + index * .83
                orbit = 74 + (index % 6) * 14
                x = cx + math.cos(angle) * orbit
                y = cy + math.sin(angle * 1.13) * orbit * .55
                size = 1.3 + (index % 3) * .55
                self.coords(dot, x-size, y-size, x+size, y+size)
            self.after(28 if mode == "Повні" else (90 if mode == "Спрощені" else 600), self._animate)
        except tk.TclError:
            pass


class ResponsiveCardGrid(tk.Frame):
    def __init__(self, parent, card_width=330, gap=16, max_columns=4):
        super().__init__(parent, bg=BG)
        self.card_width = card_width
        self.gap = gap
        self.max_columns = max_columns
        self.cards = []
        self.columns = 0
        self.bind("<Configure>", self._reflow)

    def add(self, card):
        self.cards.append(card)
        self.after_idle(self._reflow)

    def _reflow(self, _event=None):
        width = max(self.winfo_width(), self.card_width)
        columns = max(1, min(self.max_columns, (width + self.gap) // (self.card_width + self.gap)))
        if columns == self.columns and all(card.winfo_manager() for card in self.cards):
            return
        self.columns = columns
        for card in self.cards:
            card.grid_forget()
        for index, card in enumerate(self.cards):
            card.grid(
                row=index // columns,
                column=index % columns,
                padx=(0, self.gap),
                pady=(0, self.gap),
                sticky="nw",
            )


class PillButton(tk.Canvas):
    def __init__(self, parent, text, command, width=150, height=42, primary=True, danger=False):
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")
        self.command = command
        self.primary = primary
        self.danger = danger
        self.text = text
        self.enabled = True
        self.hover_mix = 0.0
        self.hover_target = 0.0
        self.hover_job = None
        self.bind("<Button-1>", lambda _e: self.command() if self.enabled else None)
        self.bind("<Enter>", lambda _e: self.animate_hover(1.0))
        self.bind("<Leave>", lambda _e: self.animate_hover(0.0))
        self.draw(False)

    def animate_hover(self, target):
        mode = getattr(self.winfo_toplevel(), "animation_mode", "Повні")
        if mode == "Вимкнені":
            self.hover_mix = 0.0
            self.hover_target = 0.0
            self.draw(False)
            return
        if mode == "Спрощені":
            self.hover_mix = target
            self.hover_target = target
            self.draw()
            return
        self.hover_target = target
        if self.hover_job is None:
            self._hover_step()

    def _hover_step(self):
        difference = self.hover_target - self.hover_mix
        if abs(difference) < 0.03:
            self.hover_mix = self.hover_target
            self.hover_job = None
            self.draw()
            return
        self.hover_mix += difference * 0.32
        self.draw()
        try:
            self.hover_job = self.after(16, self._hover_step)
        except tk.TclError:
            self.hover_job = None

    def draw(self, hover=None):
        self.delete("all")
        amount = self.hover_mix if hover is None else (1.0 if hover else 0.0)
        if not self.enabled:
            fill, outline, color = PANEL_2, LINE, "#657083"
        elif self.danger:
            fill, outline, color = blend("#351b25", "#552331", amount), "#713043", "#ff8294"
        elif self.primary:
            fill, outline, color = blend(ACCENT, ACCENT_HOVER, amount), ACCENT, "#04110f"
        else:
            fill, outline, color = blend(PANEL_2, "#1b2633", amount), blend(LINE, "#34465b", amount), TEXT
        rounded_rectangle(self, 1, 1, int(self["width"]) - 1, int(self["height"]) - 1, 13, fill=fill, outline=outline)
        self.create_text(int(self["width"]) / 2, int(self["height"]) / 2, text=self.text, fill=color, font=("Segoe UI Semibold", 10))

    def set_enabled(self, value: bool):
        self.enabled = value
        self.configure(cursor="hand2" if value else "arrow")
        self.draw(False)

    def set_layout(self, text=None, width=None, height=None):
        if text is not None:
            self.text = text
        if width is not None:
            self.configure(width=width)
        if height is not None:
            self.configure(height=height)
        self.draw(False)


class AccountDialog(tk.Toplevel):
    def __init__(self, app, required=False):
        super().__init__(app)
        self.app = app
        self.required = required
        self.mode = "login"
        self.busy = False
        self.title("UG MOD HUB — профіль")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._center()
        self.grab_set()
        self._render()
        self.after(50, self.focus_force)

    def _center(self):
        self.update_idletasks()
        width = min(500, max(420, self.winfo_screenwidth() - 40))
        desired_height = 700 if self.mode == "register" else 610
        height = min(desired_height, max(560, self.winfo_screenheight() - 68))
        x = self.app.winfo_rootx() + max(0, (self.app.winfo_width() - width) // 2)
        y = self.app.winfo_rooty() + max(0, (self.app.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _clear(self):
        for child in self.winfo_children():
            child.destroy()

    def _render(self):
        self._clear()
        self._center()
        panel = tk.Frame(self, bg=PANEL, padx=34, pady=28, highlightbackground=LINE, highlightthickness=1)
        panel.pack(fill="both", expand=True, padx=18, pady=18)
        tk.Label(panel, text="UG", bg=ACCENT, fg="#04110f", width=4, height=2, font=("Segoe UI Black", 11)).pack()
        user = self.app.account_user
        if user:
            tk.Label(panel, text="ВАШ ПРОФІЛЬ", bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 9)).pack(pady=(20, 4))
            tk.Label(panel, text=str(user.get("display_name", "Користувач")), bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 24)).pack()
            tk.Label(panel, text=str(user.get("email", "")), bg=PANEL, fg=MUTED, font=("Segoe UI", 10)).pack(pady=(5, 3))
            tk.Label(panel, text="Зареєстровано: " + format_account_date(user.get("created_at", "")), bg=PANEL, fg="#78909c", font=("Segoe UI", 9)).pack(pady=(8, 3))
            tk.Label(panel, text="● Пошта підтверджена", bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 9)).pack(pady=(8, 28))
            PillButton(panel, "Вийти з профілю", self._logout, width=260, height=44, primary=False, danger=True).pack()
            return

        tk.Label(panel, text="ОСОБИСТИЙ ПРОФІЛЬ", bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 9)).pack(pady=(18, 4))
        tk.Label(panel, text="Вхід до UG MOD HUB", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 23)).pack()
        tk.Label(panel, text="Для роботи потрібна підтверджена електронна пошта.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(pady=(5, 18))
        tabs = tk.Frame(panel, bg=PANEL)
        tabs.pack(fill="x", pady=(0, 16))
        for mode, text in (("login", "Увійти"), ("register", "Створити профіль")):
            label = tk.Label(tabs, text=text, bg=PANEL_2 if self.mode == mode else BG, fg=TEXT if self.mode == mode else MUTED, padx=16, pady=9, cursor="hand2", font=("Segoe UI Semibold", 9))
            label.pack(side="left", expand=True, fill="x", padx=2)
            label.bind("<Button-1>", lambda _event, value=mode: self._switch(value))
        self.entries = {}
        if self.mode == "register":
            self._field(panel, "Нікнейм", "display_name")
        self._field(panel, "Електронна пошта", "email")
        self._field(panel, "Пароль", "password", show="•")
        if self.mode == "register":
            self._field(panel, "Повторіть пароль", "password_confirm", show="•")
        self.status = tk.Label(panel, text="", bg=PANEL, fg=RED, wraplength=390, justify="left", font=("Segoe UI", 9))
        self.status.pack(fill="x", pady=(7, 1))
        text = "Створити та надіслати лист" if self.mode == "register" else "Увійти"
        self.submit_button = PillButton(panel, text, self._submit, width=360, height=44)
        self.submit_button.pack(pady=(5, 0))
        if self.mode == "login":
            resend = tk.Label(panel, text="Надіслати лист підтвердження повторно", bg=PANEL, fg=ACCENT, cursor="hand2", font=("Segoe UI Semibold", 8))
            resend.pack(pady=(15, 0))
            resend.bind("<Button-1>", lambda _event: self._resend())
        self.bind("<Return>", lambda _event: self._submit())

    def _field(self, parent, label, name, show=""):
        tk.Label(parent, text=label, bg=PANEL, fg="#b9c4d0", anchor="w", font=("Segoe UI Semibold", 9)).pack(fill="x", pady=(8, 5))
        entry = tk.Entry(parent, bg="#080d13", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10), show=show)
        entry.pack(fill="x", ipady=9)
        self.entries[name] = entry
        if len(self.entries) == 1:
            entry.focus_set()

    def _switch(self, mode):
        if not self.busy:
            self.mode = mode
            self._render()

    def _run(self, worker, done):
        if self.busy:
            return
        self.busy = True
        self.submit_button.set_enabled(False)
        self.status.configure(text="Зачекайте…", fg=MUTED)
        def task():
            try:
                result = worker()
                self.after(0, lambda: done(result))
            except Exception as exc:
                self.after(0, lambda message=str(exc): self._failed(message))
        threading.Thread(target=task, name="account-request", daemon=True).start()

    def _submit(self):
        values = {name: entry.get().strip() for name, entry in self.entries.items()}
        if self.mode == "register":
            if values.get("password") != values.get("password_confirm"):
                self.status.configure(text="Паролі не збігаються.", fg=RED)
                return
            self._run(
                lambda: register_account(self.app.account_url, values.get("email", ""), values.get("display_name", ""), values.get("password", "")),
                lambda _result: self._registered(values.get("email", "")),
            )
        else:
            self._run(
                lambda: login_account(self.app.account_url, values.get("email", ""), values.get("password", "")),
                self._logged_in,
            )

    def _registered(self, email):
        self.busy = False
        self.mode = "login"
        self._render()
        self.entries["email"].insert(0, email)
        self.status.configure(text="Лист надіслано. Підтвердьте пошту, а потім увійдіть.", fg=ACCENT)

    def _logged_in(self, result):
        self.busy = False
        self.app.set_account_session(str(result.get("token", "")), result.get("user") or {})
        self.destroy()

    def _resend(self):
        email = self.entries.get("email").get().strip() if self.entries.get("email") else ""
        self._run(lambda: resend_account_email(self.app.account_url, email), lambda _result: self._resent())

    def _resent(self):
        self.busy = False
        self.submit_button.set_enabled(True)
        self.status.configure(text="Новий лист надіслано. Перевірте також папку «Спам».", fg=ACCENT)

    def _failed(self, message):
        self.busy = False
        try:
            self.submit_button.set_enabled(True)
            self.status.configure(text=message or "Помилка авторизації", fg=RED)
        except tk.TclError:
            pass

    def _logout(self):
        self.app.clear_account_session(revoke=True)
        self.required = True
        self.mode = "login"
        self._render()

    def _close(self):
        if self.required and not self.app.account_user:
            self.app.close_app()
        else:
            self.destroy()


class ServerSelectDialog(tk.Toplevel):
    """Compact Nightline server picker shown before file verification."""

    def __init__(self, app, choices, selected_host, selected_port, on_select):
        super().__init__(app)
        self.app = app
        self.on_select = on_select
        self.configure(bg="#080b10")
        self.overrideredirect(True)
        self.transient(app)
        self.grab_set()
        self.bind("<Escape>", lambda _event: self.destroy())

        shell = tk.Frame(self, bg="#0d1219", padx=18, pady=16, highlightbackground="#34404d", highlightthickness=1)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg="#0d1219")
        header.pack(fill="x", pady=(0, 12))
        tk.Label(header, text="ОБЕРІТЬ СЕРВЕР", bg="#0d1219", fg=TEXT, font=("Segoe UI Semibold", 11)).pack(side="left")
        close = tk.Label(header, text="×", bg="#171d26", fg=MUTED, width=3, pady=4, cursor="hand2", font=("Segoe UI", 11))
        close.pack(side="right")
        close.bind("<Button-1>", lambda _event: self.destroy())
        tk.Label(
            shell,
            text="Після вибору UG MOD HUB перевірить установлені файли та запустить UKRAINE GTA.",
            bg="#0d1219", fg=MUTED, justify="left", wraplength=390, font=("Segoe UI", 9),
        ).pack(fill="x", pady=(0, 10))

        selected_key = (str(selected_host), int(selected_port))
        for index, item in enumerate(choices):
            key = (str(item["host"]), int(item["port"]))
            active = key == selected_key
            row = tk.Frame(
                shell,
                bg="#19212b" if active else "#121820",
                padx=10,
                pady=6,
                cursor="hand2",
                highlightbackground=ACCENT if active else "#232c37",
                highlightthickness=1,
            )
            row.pack(fill="x", pady=2)
            badge = tk.Label(
                row,
                text=f"{index + 1:02d}",
                width=4,
                height=1,
                bg=ACCENT if active else "#233249",
                fg="#171008" if active else BLUE,
                font=("Segoe UI Semibold", 8),
            )
            badge.pack(side="left")
            copy = tk.Frame(row, bg=row.cget("bg"))
            copy.pack(side="left", fill="x", expand=True, padx=10)
            name = tk.Label(copy, text=item["name"], bg=row.cget("bg"), fg=TEXT, anchor="w", font=("Segoe UI Semibold", 9))
            name.pack(fill="x")
            address = tk.Label(copy, text=f"{item['host']}:{item['port']}", bg=row.cget("bg"), fg="#697482", anchor="w", font=("Consolas", 7))
            address.pack(fill="x", pady=(2, 0))
            online = tk.Label(row, text="●", bg=row.cget("bg"), fg=GREEN, font=("Segoe UI", 9))
            online.pack(side="right")

            def choose(_event=None, value=dict(item)):
                self.destroy()
                self.on_select(value)

            for widget in (row, badge, copy, name, address, online):
                widget.bind("<Button-1>", choose)
        self.after_idle(self._center)

    def _center(self):
        try:
            self.update_idletasks()
            width = min(470, max(410, self.app.winfo_width() - 80))
            height = min(max(430, self.winfo_reqheight()), max(430, self.app.winfo_height() - 48))
            x = self.app.winfo_rootx() + (self.app.winfo_width() - width) // 2
            y = self.app.winfo_rooty() + (self.app.winfo_height() - height) // 2
            self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass


class TransferDialog(tk.Toplevel):
    def __init__(self, parent, title: str, cancel_event: threading.Event, cancellable=True):
        super().__init__(parent)
        self.cancel_event = cancel_event
        self.started = time.monotonic()
        self.last_bytes = 0
        self.title("UG MOD HUB — операція")
        self.configure(bg=BG)
        width = min(620, max(420, parent.winfo_screenwidth() - 40))
        height = min(300, max(260, parent.winfo_screenheight() - 60))
        self.geometry(f"{width}x{height}")
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.grab_set()
        box = tk.Frame(self, bg=PANEL, padx=28, pady=26, highlightbackground=LINE, highlightthickness=1)
        box.pack(fill="both", expand=True, padx=18, pady=18)
        tk.Label(box, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 17)).pack(anchor="w")
        self.file_label = tk.Label(box, text="Підготовка…", bg=PANEL, fg=MUTED, font=("Segoe UI", 10), anchor="w")
        self.file_label.pack(fill="x", pady=(14, 10))
        self.progress = ttk.Progressbar(box, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        stats = tk.Frame(box, bg=PANEL)
        stats.pack(fill="x", pady=(9, 18))
        self.percent_label = tk.Label(stats, text="0%", bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 10))
        self.percent_label.pack(side="left")
        self.speed_label = tk.Label(stats, text="Швидкість: —", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.speed_label.pack(side="left", padx=18)
        self.eta_label = tk.Label(stats, text="Залишилось: —", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.eta_label.pack(side="left")
        self.cancel_button = PillButton(box, "Скасувати операцію", self.cancel, width=190, height=42, primary=False, danger=True)
        self.cancel_button.pack(anchor="e")
        if not cancellable:
            self.cancel_button.text = "Безпечне відновлення…"
            self.cancel_button.set_enabled(False)
        self.after(20, self._center)

    def _center(self):
        try:
            self.update_idletasks()
            x = self.master.winfo_rootx() + (self.master.winfo_width() - self.winfo_width()) // 2
            y = self.master.winfo_rooty() + (self.master.winfo_height() - self.winfo_height()) // 2
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def cancel(self):
        self.cancel_event.set()
        self.cancel_button.set_enabled(False)
        self.cancel_button.text = "Скасування…"
        self.cancel_button.draw(False)
        self.file_label.configure(text="Завершуємо поточний безпечний крок…", fg="#e4a853")

    def update_progress(self, current, total, filename, bytes_done=0, total_bytes=0):
        if not self.winfo_exists():
            return
        if total_bytes:
            ratio = min(1.0, bytes_done / total_bytes)
        else:
            ratio = min(1.0, current / max(total, 1))
        elapsed = max(time.monotonic() - self.started, 0.05)
        speed = bytes_done / elapsed
        eta = (total_bytes - bytes_done) / speed if total_bytes and speed > 0 else None
        self.progress.configure(value=ratio * 100)
        self.percent_label.configure(text=f"{ratio * 100:.0f}%  •  {current}/{max(total, 1)}")
        self.file_label.configure(text=filename or "Підготовка…", fg=TEXT)
        self.speed_label.configure(text=f"Швидкість: {self._size(speed)}/с" if speed else "Швидкість: —")
        self.eta_label.configure(text=f"Залишилось: {self._duration(eta)}" if eta is not None else "Залишилось: —")

    @staticmethod
    def _size(value):
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if value < 1024 or unit == "ГБ":
                return f"{value:.1f} {unit}"
            value /= 1024

    @staticmethod
    def _duration(seconds):
        seconds = max(0, int(seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"


class SkinTargetDialog(tk.Toplevel):
    def __init__(self, parent, targets: list[SkinTarget], kind_label: str = "скін"):
        super().__init__(parent)
        self.result: SkinTarget | None = None
        self.targets = targets
        self.visible_targets: list[SkinTarget] = []
        self.kind_label = kind_label
        self.title(f"Оберіть {kind_label} для заміни")
        self.configure(bg=BG)
        self.geometry("760x620")
        self.minsize(620, 460)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        header = tk.Frame(self, bg=PANEL, padx=24, pady=18)
        header.pack(fill="x")
        tk.Label(header, text=f"Який {kind_label} потрібно замінити?", bg=PANEL, fg=TEXT,
                 font=("Segoe UI Semibold", 20)).pack(anchor="w")
        tk.Label(header, text="Почніть вводити назву для швидкого пошуку.", bg=PANEL,
                 fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))

        content = tk.Frame(self, bg=BG, padx=24, pady=20)
        content.pack(fill="both", expand=True)
        self.query = tk.StringVar()
        search = tk.Entry(content, textvariable=self.query, bg="#101722", fg=TEXT,
                          insertbackground=TEXT, relief="flat", font=("Segoe UI", 11))
        search.pack(fill="x", ipady=10, pady=(0, 12))
        self.query.trace_add("write", lambda *_args: self.refresh())

        list_frame = tk.Frame(content, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        list_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            list_frame, bg=PANEL, fg=TEXT, selectbackground="#123b3a",
            selectforeground=TEXT, activestyle="none", relief="flat",
            highlightthickness=0, font=("Consolas", 11), yscrollcommand=scrollbar.set,
        )
        self.listbox.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        scrollbar.configure(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", lambda _event: self.accept())
        self.listbox.bind("<Return>", lambda _event: self.accept())

        footer = tk.Frame(content, bg=BG)
        footer.pack(fill="x", pady=(14, 0))
        PillButton(footer, "Скасувати", self.destroy, width=130, primary=False).pack(side="right")
        PillButton(footer, f"Обрати {kind_label}", self.accept, width=170).pack(side="right", padx=(0, 10))
        self.refresh()
        search.focus_set()
        self.grab_set()

    def refresh(self):
        query = self.query.get().strip().casefold()
        self.visible_targets = []
        self.listbox.delete(0, "end")
        for target in self.targets:
            haystack = target.name.casefold()
            if query and query not in haystack:
                continue
            self.visible_targets.append(target)
            self.listbox.insert(
                "end",
                target.name,
            )
        if self.visible_targets:
            self.listbox.selection_set(0)

    def accept(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        self.result = self.visible_targets[selection[0]]
        self.destroy()


class FPSOverlay:
    def __init__(self, app: "ModHub"):
        self.app = app
        self.enabled = False
        self.shutdown = threading.Event()
        self.thread: threading.Thread | None = None
        self.capture: subprocess.Popen | None = None
        self.window: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.text_item: int | None = None

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if self.enabled:
            self.shutdown.clear()
            self._ensure_window()
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._worker, name="fps-overlay", daemon=True)
                self.thread.start()
        else:
            self.shutdown.set()
            self._stop_capture()
            self._hide()

    def _ensure_window(self):
        if self.window and self.window.winfo_exists():
            return
        window = tk.Toplevel(self.app)
        window.overrideredirect(True)
        window.configure(bg="#010203")
        window.attributes("-topmost", True)
        try:
            window.wm_attributes("-transparentcolor", "#010203")
        except tk.TclError:
            pass
        canvas = tk.Canvas(window, width=96, height=36, bg="#010203", highlightthickness=0, bd=0)
        canvas.pack()
        rounded_rectangle(canvas, 3, 4, 95, 35, 10, fill="#020507", outline="")
        rounded_rectangle(canvas, 1, 1, 93, 33, 10, fill="#081117", outline="#1d3d42")
        text_item = canvas.create_text(
            47, 17, text="FPS  —", fill="#7cf4df",
            font=("Segoe UI Semibold", 11),
        )
        window.withdraw()
        self.window, self.canvas, self.text_item = window, canvas, text_item
        window.update_idletasks()
        if os.name == "nt":
            try:
                import ctypes
                hwnd = window.winfo_id()
                get_long = ctypes.windll.user32.GetWindowLongW
                set_long = ctypes.windll.user32.SetWindowLongW
                exstyle = get_long(hwnd, -20)
                set_long(hwnd, -20, exstyle | 0x00000020 | 0x00000080 | 0x08000000)
            except Exception:
                pass

    def _show(self):
        if not self.enabled:
            return
        self._ensure_window()
        try:
            width = max(self.window.winfo_reqwidth(), 96)
            height = max(self.window.winfo_reqheight(), 36)
            x = self.window.winfo_screenwidth() - width - 14
            y = self.window.winfo_screenheight() - height - 16
            self.window.geometry(f"{width}x{height}+{x}+{y}")
            self.window.deiconify()
            self.window.lift()
        except tk.TclError:
            pass

    def _hide(self):
        try:
            if self.window and self.window.winfo_exists():
                self.window.withdraw()
        except tk.TclError:
            pass

    def _update(self, fps: float):
        if not self.enabled or not self.canvas or self.text_item is None:
            return
        color = "#7cf4df" if fps >= 50 else ("#ffd166" if fps >= 30 else "#ff7188")
        try:
            self.canvas.itemconfigure(self.text_item, text=f"FPS  {round(fps)}", fill=color)
            self._show()
        except tk.TclError:
            pass

    def _worker(self):
        binary = RESOURCE_DIR / "tools" / "PresentMon.exe"
        if not binary.is_file():
            self.app.after(0, lambda: self.app.set_status("Лічильник FPS недоступний: немає PresentMon"))
            return
        while not self.shutdown.wait(0.5):
            if "gta_sa.exe" not in running_game_processes():
                self.app.after(0, self._hide)
                continue
            command = [
                str(binary), "--process_name", "gta_sa.exe", "--output_stdout",
                "--no_console_stats", "--v1_metrics", "--no_track_gpu",
                "--no_track_input", "--no_track_display", "--terminate_on_proc_exit",
                "--session_name", "MTA_MOD_HUB_FPS", "--stop_existing_session",
            ]
            try:
                self.capture = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                reader = csv.DictReader(self.capture.stdout)
                samples: list[float] = []
                last_update = 0.0
                for row in reader:
                    if self.shutdown.is_set() or not self.enabled:
                        break
                    value = None
                    for key, raw in row.items():
                        normalized = (key or "").replace("_", "").casefold()
                        if "betweenpresents" in normalized or "betweenappstart" in normalized:
                            try:
                                value = float(raw)
                            except (TypeError, ValueError):
                                value = None
                            if value:
                                break
                    if not value or value <= 0:
                        continue
                    samples.append(1000.0 / value)
                    samples = samples[-30:]
                    now = time.monotonic()
                    if now - last_update >= 0.2:
                        fps = sum(samples) / len(samples)
                        self.app.after(0, lambda current=fps: self._update(current))
                        last_update = now
            except (OSError, ValueError):
                self.app.after(0, lambda: self.app.set_status("Не вдалося запустити вимірювання FPS"))
            finally:
                self._stop_capture()
                self.app.after(0, self._hide)

    def _stop_capture(self):
        process = self.capture
        self.capture = None
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    def close(self):
        self.enabled = False
        self.shutdown.set()
        self._stop_capture()
        try:
            if self.window and self.window.winfo_exists():
                self.window.destroy()
        except tk.TclError:
            pass


class ModCard(tk.Frame):
    def __init__(self, parent, app: "ModHub", mod: Mod):
        super().__init__(parent, bg=PANEL, width=330, height=360, highlightbackground=LINE, highlightthickness=1)
        self.pack_propagate(False)
        self.app = app
        self.mod = mod
        self.hover_amount = 0.0
        self.hover_target = 0.0
        self.hover_job = None

        image = app.mod_image(mod, (328, 166))
        image_label = tk.Label(self, image=image, bg=PANEL, bd=0)
        image_label.image = image
        image_label.pack(fill="x")

        self.accent_rail = tk.Canvas(self, height=4, bg="#0b1720", highlightthickness=0, bd=0)
        self.accent_rail.pack(fill="x")
        self.rail_base = self.accent_rail.create_rectangle(0, 0, 330, 4, fill="#10232d", outline="")
        self.rail_fill = self.accent_rail.create_rectangle(0, 0, 78, 4, fill=mod.accent, outline="")
        self.accent_rail.bind("<Configure>", lambda _event: self._draw_accent_rail())

        body = tk.Frame(self, bg=PANEL, padx=18, pady=14)
        body.pack(fill="both", expand=True)
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text=mod.title, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 14), anchor="w").pack(side="left", fill="x", expand=True)
        self.status = tk.Label(row, bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 9))
        self.status.pack(side="right")
        self.favorite = tk.Label(row, text="☆", bg=PANEL, fg="#d7b85b", cursor="hand2", font=("Segoe UI Symbol", 17))
        self.favorite.pack(side="right", padx=(0, 8))
        self.favorite.bind("<Button-1>", lambda _event: app.toggle_favorite(mod))
        tk.Label(body, text=mod.category.upper(), bg=PANEL, fg=mod.accent, font=("Segoe UI Semibold", 8), anchor="w").pack(fill="x", pady=(8, 3))
        tk.Label(body, text=mod.description, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=292, justify="left", anchor="nw").pack(fill="x", pady=(0, 10))

        bottom = tk.Frame(body, bg=PANEL)
        bottom.pack(side="bottom", fill="x")
        self.button = PillButton(bottom, "Встановити", lambda: app.install(mod), width=292, height=40)
        self.button.pack(side="left")
        self._bind_hover_tree(self)
        self.refresh()

    def _draw_accent_rail(self):
        try:
            width = max(1, self.accent_rail.winfo_width())
            fill_width = width * (.24 + self.hover_amount * .76)
            self.accent_rail.coords(self.rail_base, 0, 0, width, 4)
            self.accent_rail.coords(self.rail_fill, 0, 0, fill_width, 4)
            self.accent_rail.itemconfigure(
                self.rail_fill,
                fill=blend(self.mod.accent, ACCENT, self.hover_amount * .42),
            )
        except tk.TclError:
            pass

    def _bind_hover_tree(self, widget):
        widget.bind("<Enter>", lambda _event=None: self.animate_hover(1.0), add="+")
        widget.bind("<Leave>", lambda _event=None: self._check_leave(), add="+")
        for child in widget.winfo_children():
            self._bind_hover_tree(child)

    def _check_leave(self):
        self.after(5, lambda: self.animate_hover(0.0) if not self._pointer_inside() else None)

    def _pointer_inside(self):
        try:
            x, y = self.winfo_pointerxy()
            return self.winfo_containing(x, y) is not None and self._is_descendant(self.winfo_containing(x, y))
        except tk.TclError:
            return False

    def _is_descendant(self, widget):
        while widget is not None:
            if widget == self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def animate_hover(self, target):
        if self.app.animation_mode == "Вимкнені":
            return
        if self.app.animation_mode == "Спрощені":
            self.hover_amount = target
            self.configure(highlightbackground=blend(LINE, self.mod.accent, target))
            self._draw_accent_rail()
            return
        self.hover_target = target
        if self.hover_job is None:
            self._hover_step()

    def animate_intro(self, delay=0):
        if self.app.animation_mode != "Повні":
            return
        self.hover_amount = 0.85
        self.hover_target = 0.0
        self.configure(highlightbackground=blend(LINE, self.mod.accent, self.hover_amount))
        self.after(delay, lambda: self.animate_hover(0.0))

    def _hover_step(self):
        difference = self.hover_target - self.hover_amount
        if abs(difference) < 0.03:
            self.hover_amount = self.hover_target
            self.hover_job = None
        else:
            self.hover_amount += difference * 0.28
            try:
                self.hover_job = self.after(16, self._hover_step)
            except tk.TclError:
                self.hover_job = None
        try:
            self.configure(highlightbackground=blend(LINE, self.mod.accent, self.hover_amount))
            self._draw_accent_rail()
        except tk.TclError:
            return

    def refresh(self):
        self.favorite.configure(text="★" if self.mod.id in load_favorites() else "☆")
        installed = self.mod.id in load_state().get("installed", {})
        available = mod_payload_available(self.mod.id)
        if installed:
            self.status.config(text="● ВСТАНОВЛЕНО", fg=GREEN)
            self.button.text = "Видалити"
            self.button.primary = False
            self.button.danger = True
            self.button.command = lambda: self.app.remove(self.mod)
            self.button.set_enabled(True)
        else:
            catalog_status = mod_catalog_status(self.mod)
            if catalog_status == "paused":
                self.status.config(text="● ПРИЗУПИНЕНО", fg=RED)
                self.button.text = "Мод призупинено"
                self.button.primary = False
                self.button.danger = False
                self.button.set_enabled(False)
            elif catalog_status == "development" or not available:
                self.status.config(text="● У РОЗРОБЦІ", fg="#e4a853")
                self.button.text = "Мод у розробці"
                self.button.primary = False
                self.button.danger = False
                self.button.set_enabled(False)
            else:
                self.status.config(text="ДОСТУПНО", fg=ACCENT)
                self.button.text = "Встановити"
                self.button.primary = True
                self.button.danger = False
                self.button.command = lambda: self.app.install(self.mod)
                self.button.set_enabled(True)


class ModHub(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.withdraw()
        self.catalog, self.mods = load_catalog()
        self.dev_mode = is_dev_mode()
        self.settings = load_settings()
        self.account_user = dict(self.settings.get("account_user") or {})
        self.account_url = str(self.settings.get("account_url") or DEFAULT_ACCOUNT_URL)
        self.account_dialog = None
        self.server_dialog = None
        self.animation_mode = self.settings.get("animation_mode", "Повні")
        self.admin_mode = is_admin()
        self.active_category = "Головна"
        self.cards: list[ModCard] = []
        self._images: dict[str, ImageTk.PhotoImage] = {}
        self.busy = False
        self.editing_mod_id: str | None = None
        self.guard_enabled = bool(self.settings.get("resource_guard", True))
        self.fps_counter_enabled = bool(self.settings.get("fps_counter", False))
        self.guard_shutdown = threading.Event()
        self.guard_hashes: dict[str, tuple[int, int, str]] = {}
        self.guard_thread: threading.Thread | None = None
        self.animation_phase = 0.0
        self._maximized = False
        self._restore_geometry: str | None = None
        self._drag_offset = (0, 0)
        self._resize_state = None
        self._responsive_job = None
        self._layout_mode = None
        self._update_checked = False
        self._startup_check_running = False
        self._startup_attempt = 0
        self._online_services_started = False
        self._catalog_sync_running = False
        self._catalog_retry_pending = False
        self._window_minimized = False

        self.title(self.catalog.get("app_name", "UG MOD HUB"))
        icon_path = RESOURCE_DIR / "assets" / "ukraine_gta_app_icon.ico"
        if not icon_path.exists():
            icon_path = RESOURCE_DIR / "assets" / "app_icon.png"
        if icon_path.exists():
            try:
                self.window_icon = ImageTk.PhotoImage(Image.open(icon_path).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS))
                self.iconphoto(True, self.window_icon)
            except (OSError, tk.TclError):
                self.window_icon = None
        area_x, area_y, area_width, area_height = self._primary_work_area()
        self.minimum_width = min(960, area_width)
        self.minimum_height = min(620, area_height)
        target_width = min(1540, max(self.minimum_width, round(area_width * 0.94)))
        target_height = min(960, max(self.minimum_height, round(area_height * 0.92)))
        target_x = area_x + max(0, (area_width - target_width) // 2)
        target_y = area_y + max(0, (area_height - target_height) // 2)
        self.geometry(f"{target_width}x{target_height}+{target_x}+{target_y}")
        self.minsize(self.minimum_width, self.minimum_height)
        self.configure(bg=BG)
        self._setup_style()
        self._build_shell()
        self.fps_overlay = FPSOverlay(self)
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.bind("<Map>", self._on_window_map, add="+")
        self.startup_splash = StartupSplash(self, self._finish_startup)
        self.after(180, self._startup_tasks)
        self.after(80, self.animate_ambient)

    def _primary_work_area(self):
        if os.name == "nt":
            try:
                import ctypes

                class Rect(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                rect = Rect()
                if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _build_titlebar(self):
        bar = tk.Frame(self, bg="#05070b", height=34, highlightbackground=LINE, highlightthickness=0)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        identity = tk.Frame(bar, bg="#05070b")
        identity.pack(side="left", fill="y", padx=(12, 0))
        icon_path = RESOURCE_DIR / "assets" / "nightline" / "ug-logo.png"
        if icon_path.is_file():
            try:
                self.titlebar_icon = ImageTk.PhotoImage(
                    Image.open(icon_path).convert("RGBA").resize((20, 20), Image.Resampling.LANCZOS)
                )
                tk.Label(identity, image=self.titlebar_icon, bg="#05070b", bd=0).pack(side="left", pady=7)
            except (OSError, tk.TclError):
                self.titlebar_icon = None
        title = tk.Label(
            identity,
            text="UG MOD HUB",
            bg="#05070b", fg="#9ca5b1", font=("Segoe UI Semibold", 9),
        )
        title.pack(side="left", padx=(8, 0), fill="y")

        controls = tk.Frame(bar, bg="#05070b")
        controls.pack(side="right", fill="y")

        def control(text, command, hover="#18222d", width=48):
            label = tk.Label(
                controls, text=text, bg="#05070b", fg="#aeb6c0",
                width=width // 8, cursor="hand2", font=("Segoe UI Symbol", 11),
            )
            label.pack(side="left", fill="y")
            label.bind("<Button-1>", lambda _event: command())
            label.bind("<Enter>", lambda _event: label.configure(bg=hover, fg=TEXT))
            label.bind("<Leave>", lambda _event: label.configure(bg="#05070b", fg="#aeb6c0"))
            return label

        self.minimize_button = control("—", self._minimize_window)
        self.maximize_button = control("□", self._toggle_maximize)
        self.close_button = control("×", self.close_app, hover="#d94b5f", width=52)

        for widget in (bar, identity, title):
            widget.bind("<Button-1>", self._start_window_drag)
            widget.bind("<B1-Motion>", self._drag_window)
            widget.bind("<Double-Button-1>", lambda _event: self._toggle_maximize())
        self.titlebar = bar

    def _window_handle(self):
        self.update_idletasks()
        hwnd = int(self.winfo_id())
        if os.name == "nt":
            try:
                import ctypes
                # winfo_id() points at Tk's client window.  The taskbar style
                # belongs to the native wrapper created by Windows/Tk.
                parent = int(ctypes.windll.user32.GetParent(hwnd))
                if parent:
                    hwnd = parent
            except Exception:
                pass
        return hwnd

    def _apply_window_chrome(self):
        if os.name != "nt":
            return
        try:
            import ctypes
            hwnd = self._window_handle()
            user32 = ctypes.windll.user32
            exstyle = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, (exstyle | 0x00040000) & ~0x00000080)
            # Notify Explorer that this frameless window is a regular app
            # window, so it gets its own active button on the taskbar.
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
            corner = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
        except Exception:
            pass

    def _minimize_window(self):
        if self._window_minimized:
            return
        self._window_minimized = True
        try:
            if os.name == "nt":
                import ctypes
                self._apply_window_chrome()
                ctypes.windll.user32.ShowWindow(self._window_handle(), 6)
            else:
                self.iconify()
        except (tk.TclError, OSError):
            self._window_minimized = False

    def _on_window_map(self, event):
        if event.widget is not self or not self._window_minimized:
            return
        self.after(30, self._restore_after_minimize)

    def _restore_after_minimize(self):
        try:
            if self.state() == "iconic":
                return
            self._apply_window_chrome()
            self._window_minimized = False
        except tk.TclError:
            pass

    def _current_work_area(self):
        if os.name == "nt":
            try:
                import ctypes

                class Rect(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                class MonitorInfo(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", Rect), ("rcWork", Rect), ("dwFlags", ctypes.c_ulong)]

                user32 = ctypes.windll.user32
                monitor = user32.MonitorFromWindow(self._window_handle(), 2)
                info = MonitorInfo()
                info.cbSize = ctypes.sizeof(MonitorInfo)
                if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    work = info.rcWork
                    return work.left, work.top, work.right - work.left, work.bottom - work.top
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _toggle_maximize(self):
        if self._maximized:
            geometry = self._restore_geometry or "1320x820+80+60"
            self.geometry(geometry)
            self._maximized = False
            self.maximize_button.configure(text="□")
        else:
            self._restore_geometry = self.geometry()
            x, y, width, height = self._current_work_area()
            self.geometry(f"{width}x{height}+{x}+{y}")
            self._maximized = True
            self.maximize_button.configure(text="❐")

    def _start_window_drag(self, event):
        if self._maximized:
            pointer_ratio = event.x_root / max(1, self.winfo_width())
            self._toggle_maximize()
            width = self.winfo_width()
            self.geometry(f"+{round(event.x_root - width * pointer_ratio)}+{max(0, event.y_root - 18)}")
        self._drag_offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_window(self, event):
        if self._maximized:
            return
        offset_x, offset_y = self._drag_offset
        self.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _build_resize_handles(self):
        definitions = [
            ("n", "sb_v_double_arrow", {"x": 6, "y": 0, "relwidth": 1, "width": -12, "height": 5}),
            ("s", "sb_v_double_arrow", {"x": 6, "rely": 1, "y": -5, "relwidth": 1, "width": -12, "height": 5}),
            ("w", "sb_h_double_arrow", {"x": 0, "y": 6, "width": 5, "relheight": 1, "height": -12}),
            ("e", "sb_h_double_arrow", {"relx": 1, "x": -5, "y": 6, "width": 5, "relheight": 1, "height": -12}),
            ("nw", "size_nw_se", {"x": 0, "y": 0, "width": 8, "height": 8}),
            ("ne", "size_ne_sw", {"relx": 1, "x": -8, "y": 0, "width": 8, "height": 8}),
            ("sw", "size_ne_sw", {"x": 0, "rely": 1, "y": -8, "width": 8, "height": 8}),
            ("se", "size_nw_se", {"relx": 1, "rely": 1, "x": -8, "y": -8, "width": 8, "height": 8}),
        ]
        self.resize_handles = []
        for edges, cursor, placement in definitions:
            handle = tk.Frame(self, bg=BG, cursor=cursor)
            handle.place(**placement)
            handle.bind("<Button-1>", lambda event, value=edges: self._start_resize(event, value))
            handle.bind("<B1-Motion>", self._resize_window)
            handle.lift()
            self.resize_handles.append(handle)

    def _start_resize(self, event, edges):
        if self._maximized:
            self._resize_state = None
            return
        self._resize_state = {
            "edges": edges,
            "pointer_x": event.x_root,
            "pointer_y": event.y_root,
            "x": self.winfo_x(),
            "y": self.winfo_y(),
            "width": self.winfo_width(),
            "height": self.winfo_height(),
        }

    def _resize_window(self, event):
        state = self._resize_state
        if not state or self._maximized:
            return
        dx = event.x_root - state["pointer_x"]
        dy = event.y_root - state["pointer_y"]
        x, y = state["x"], state["y"]
        width, height = state["width"], state["height"]
        edges = state["edges"]
        if "e" in edges:
            width += dx
        if "s" in edges:
            height += dy
        if "w" in edges:
            width -= dx
            x += dx
        if "n" in edges:
            height -= dy
            y += dy
        minimum_width, minimum_height = self.minimum_width, self.minimum_height
        if width < minimum_width:
            if "w" in edges:
                x -= minimum_width - width
            width = minimum_width
        if height < minimum_height:
            if "n" in edges:
                y -= minimum_height - height
            height = minimum_height
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _startup_tasks(self):
        if self._startup_check_running or self.startup_splash.finished:
            return
        self._startup_check_running = True
        self._startup_attempt += 1
        attempt = self._startup_attempt
        self.startup_splash.set_status("Перевірка зв’язку з вебсервером")
        health_url = str(self.settings.get("health_url") or DEFAULT_WEB_HEALTH_URL)

        def worker():
            try:
                result = check_web_hosting(health_url)
                trusted_origin = is_trusted_origin_url(health_url)
                catalog_url = DEFAULT_CATALOG_URL if trusted_origin else str(result.get("catalog_url", "")).strip()
                public_key = str(result.get("public_key", "")).strip()
                if not catalog_url or not public_key:
                    raise ModError("Вебсервер не повернув адресу каталогу або ключ підпису")
                result = dict(result)
            except Exception as exc:
                try:
                    message = str(exc) or "Немає зв’язку з вебсервером"
                    self.after(0, lambda text=message, token=attempt: self._startup_connection_failed(text, token))
                except tk.TclError:
                    pass
                return
            try:
                self.after(0, lambda payload=result, url=health_url, token=attempt: self._startup_connection_ready(payload, url, token))
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="web-health-check", daemon=True).start()

    def _startup_connection_failed(self, message="Немає зв’язку з вебсервером", attempt=None):
        if attempt is not None and attempt != self._startup_attempt:
            return
        self._startup_check_running = False
        self.startup_splash.mark_error(message, self._startup_tasks)

    def _startup_connection_ready(self, payload, health_url, attempt=None):
        if attempt is not None and attempt != self._startup_attempt:
            return
        if not self._startup_check_running or self.startup_splash.error_mode:
            return
        self._startup_check_running = False
        changed = False
        trusted_origin = is_trusted_origin_url(health_url)
        for setting, response_key in (
            ("catalog_url", "catalog_url"),
            ("update_url", "update_url"),
            ("account_url", "account_url"),
            ("catalog_public_key", "public_key"),
        ):
            if trusted_origin and setting == "catalog_url":
                value = DEFAULT_CATALOG_URL
            elif trusted_origin and setting == "update_url":
                value = DEFAULT_UPDATE_URL
            else:
                value = str(payload.get(response_key, "")).strip() if isinstance(payload, dict) else ""
            if value and self.settings.get(setting) != value:
                self.settings[setting] = value
                changed = True
        if changed:
            save_settings(self.settings)
        self.catalog, self.mods = load_catalog()
        self.startup_splash.set_status("Перевірка папки гри")
        self.after(25, self._complete_startup_local)

    def _complete_startup_local(self):
        if self.startup_splash.finished or self.startup_splash.error_mode:
            return
        try:
            self.first_run()
        finally:
            self.startup_splash.mark_ready()

    def _finish_startup(self):
        try:
            # Register the native wrapper before its first visible frame.
            self._apply_window_chrome()
            self.deiconify()
            self._apply_window_chrome()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass
        if not self._online_services_started:
            self._online_services_started = True
            self.after(120, self.ensure_account_session)
            self.after(300, self.start_guard_worker)
            self.after(500, lambda: self.fps_overlay.set_enabled(self.fps_counter_enabled))
            self.after(900, self._start_catalog_sync)
            self.after(1600, self.check_for_application_update)

    def _start_catalog_sync(self, announce=False):
        """Refresh catalog quietly; a slow server must never block the application."""
        if self._catalog_sync_running:
            if announce:
                self.set_status("Каталог уже оновлюється у фоні")
            return
        url = str(self.settings.get("catalog_url") or DEFAULT_CATALOG_URL).strip()
        key = str(self.settings.get("catalog_public_key") or "").strip()
        if not url or not key:
            return
        self._catalog_sync_running = True
        self._catalog_retry_pending = False
        if announce:
            self.set_status("Оновлення каталогу у фоні…")

        def worker():
            try:
                result = update_online_catalog(url, key)
            except Exception as exc:
                try:
                    self.after(0, lambda error=exc: self._catalog_sync_failed(error, announce))
                except tk.TclError:
                    pass
                return
            try:
                self.after(0, lambda payload=result: self._catalog_sync_done(payload, announce))
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="catalog-background-sync", daemon=True).start()

    def _catalog_sync_done(self, result, announce=False):
        self._catalog_sync_running = False
        self._catalog_retry_pending = False
        self.catalog, self.mods = load_catalog()
        if announce:
            self.set_status(f"Каталог оновлено · модів: {result.get('mods', len(self.mods))}")

    def _catalog_sync_failed(self, _error, announce=False):
        self._catalog_sync_running = False
        if announce:
            self.set_status("Використовується збережений каталог · повторна перевірка буде автоматично")
        if not self._catalog_retry_pending:
            self._catalog_retry_pending = True
            self.after(30000, self._retry_catalog_sync)

    def _retry_catalog_sync(self):
        self._catalog_retry_pending = False
        self._start_catalog_sync(False)

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Vertical.TScrollbar", background=PANEL_2, troughcolor=BG, bordercolor=BG, arrowcolor=MUTED)
        style.configure("TProgressbar", troughcolor=PANEL_2, background=ACCENT, borderwidth=0)
        style.configure("Dark.TCombobox", fieldbackground="#090d13", background=PANEL_2, foreground=TEXT, arrowcolor=MUTED, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", "#090d13")], foreground=[("readonly", TEXT)], selectbackground=[("readonly", "#090d13")], selectforeground=[("readonly", TEXT)])

    def _build_shell(self):
        self._build_titlebar()
        self.top = tk.Frame(self, bg="#0a0d12", height=66, padx=18, pady=10)
        self.top.pack(fill="x")
        self.top.pack_propagate(False)
        self.brand = tk.Frame(self.top, bg="#0a0d12")
        self.brand.pack(side="left")
        self.logo = tk.Canvas(self.brand, width=44, height=44, bg="#0a0d12", highlightthickness=0)
        self.logo_shape = rounded_rectangle(self.logo, 1, 1, 43, 43, 13, fill="#111720", outline="#253443")
        logo_path = RESOURCE_DIR / "assets" / "nightline" / "ug-logo.png"
        if logo_path.is_file():
            try:
                self.nightline_logo = ImageTk.PhotoImage(Image.open(logo_path).convert("RGBA").resize((36, 36), Image.Resampling.LANCZOS))
                self.logo.create_image(22, 22, image=self.nightline_logo)
            except (OSError, tk.TclError):
                self.logo.create_text(22, 22, text="UG", fill=BLUE, font=("Segoe UI Black", 11))
        else:
            self.logo.create_text(22, 22, text="UG", fill=BLUE, font=("Segoe UI Black", 11))
        self.logo.pack(side="left")
        self.brand_name = tk.Label(self.brand, text="UG MOD", fg=TEXT, bg="#0a0d12", font=("Segoe UI Semibold", 17))
        self.brand_name.pack(side="left", padx=(12, 0))
        self.brand_hub = tk.Label(self.brand, text="HUB", fg=BLUE, bg="#0a0d12", font=("Segoe UI Semibold", 17))
        self.brand_hub.pack(side="left", padx=(5, 0))

        self.search_var = tk.StringVar()
        self.search_box = tk.Frame(self.top, bg="#111720", padx=12, pady=7, highlightbackground="#27313d", highlightthickness=1)
        self.search_box.pack(side="left", padx=(38, 12))
        tk.Label(self.search_box, text="⌕", bg="#111720", fg="#65707e", font=("Segoe UI Symbol", 12)).pack(side="left")
        self.search_entry = tk.Entry(self.search_box, textvariable=self.search_var, width=25, bg="#111720", fg=TEXT, insertbackground=TEXT, relief="flat", borderwidth=0, font=("Segoe UI", 9))
        self.search_entry.pack(side="left", padx=(8, 0), ipady=2)
        self.search_entry.bind("<KeyRelease>", self.on_search)
        self.search_entry.bind("<Escape>", lambda _event: self.clear_search())

        self.top_actions = tk.Frame(self.top, bg="#0a0d12")
        self.top_actions.pack(side="right")
        self.guard_button = PillButton(self.top_actions, "Захист: LIVE", self.toggle_resource_guard, width=126, height=42, primary=False)
        self.guard_button.pack(side="left", padx=(0, 10))
        self.quick_button = PillButton(self.top_actions, "Швидке встановлення", self.quick_apply, width=165, height=42, primary=False)
        self.quick_button.pack(side="left", padx=(0, 10))
        self.account_button = PillButton(self.top_actions, "Профіль", self.open_account_dialog, width=125, height=42, primary=False)
        self.account_button.pack(side="left")

        self.accent_bar = AnimatedAccentBar(self, self)
        self.accent_bar.pack(fill="x")

        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(main, bg=RAIL_BG, width=86, padx=8, pady=14)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.content_host = tk.Frame(main, bg=BG)
        self.content_host.pack(side="left", fill="both", expand=True)
        self._build_navigation()
        self.show_home()

        self.statusbar = tk.Frame(self, bg="#0a0d12", height=58, padx=18, highlightbackground="#1b222c", highlightthickness=1)
        self.statusbar.pack(fill="x", side="bottom")
        self.statusbar.pack_propagate(False)
        status_group = tk.Frame(self.statusbar, bg="#0a0d12")
        status_group.pack(side="left", fill="y")
        self.root_status = tk.Label(status_group, text="Гру не знайдено", fg=MUTED, bg="#0a0d12", font=("Segoe UI Semibold", 8))
        self.root_status.pack(anchor="w", pady=(10, 0))
        self.status_text = tk.Label(status_group, text="", bg="#0a0d12", fg=MUTED, font=("Segoe UI", 8))
        self.status_text.pack(anchor="w", pady=(1, 7))
        self.progress = ttk.Progressbar(self.statusbar, length=220, mode="determinate")
        self.play_button = PillButton(self.statusbar, "▶  Грати в UKRAINE GTA", self.open_server_selector, width=230, height=42)
        self.play_button.pack(side="right", pady=7)
        self.update_root_status()
        self.refresh_guard_button()
        self._build_resize_handles()
        self.bind("<Configure>", self._schedule_responsive_layout, add="+")
        self.after_idle(self._apply_responsive_layout)

    def _build_navigation(self):
        items = [
            ("Головна", "⌂", "Головна"),
            ("Усі моди", "▦", "Каталог"),
            ("Обране", "♡", "Обране"),
            ("Встановлено", "✓", "Мої моди"),
            ("Історія", "↺", "Історія"),
        ]
        self.nav_buttons = {}
        self.nav_icons = {}
        self.nav_short_labels = {}
        for text, icon, short_label in items:
            button = tk.Label(self.sidebar, text=f"{icon}\n{short_label}", bg=RAIL_BG, fg=MUTED, justify="center", padx=3, pady=7, cursor="hand2", font=("Segoe UI Semibold", 8))
            button.pack(fill="x", pady=2)
            button.bind("<Button-1>", lambda _e, name=text: self.navigate(name))
            button.bind("<Enter>", lambda _e, widget=button: widget.configure(bg=PANEL_2) if widget.cget("fg") not in (TEXT, ACCENT) else None)
            button.bind("<Leave>", lambda _e, widget=button: widget.configure(bg=RAIL_BG) if widget.cget("fg") not in (TEXT, ACCENT) else None)
            self.nav_buttons[text] = button
            self.nav_icons[text] = icon
            self.nav_short_labels[text] = short_label
        self.nav_separator = tk.Frame(self.sidebar, bg=LINE, height=1)
        self.nav_separator.pack(fill="x", side="bottom", pady=8)
        settings = tk.Label(self.sidebar, text="⚙\nНалашт.", bg=RAIL_BG, fg=MUTED, justify="center", padx=3, pady=7, cursor="hand2", font=("Segoe UI Semibold", 8))
        settings.pack(fill="x", side="bottom", pady=2)
        settings.bind("<Button-1>", lambda _e: self.show_settings())
        settings.bind("<Enter>", lambda _e: settings.configure(bg=PANEL_2) if settings.cget("fg") not in (TEXT, ACCENT) else None)
        settings.bind("<Leave>", lambda _e: settings.configure(bg=RAIL_BG) if settings.cget("fg") not in (TEXT, ACCENT) else None)
        self.nav_buttons["Налаштування"] = settings
        self.nav_icons["Налаштування"] = "⚙"
        self.nav_short_labels["Налаштування"] = "Налашт."
        if self.dev_mode:
            dev = tk.Label(self.sidebar, text="◈\nDEV", bg=RAIL_BG, fg="#c28cff", justify="center", padx=3, pady=7, cursor="hand2", font=("Segoe UI Semibold", 8))
            dev.pack(fill="x", side="bottom", pady=2)
            dev.bind("<Button-1>", lambda _e: self.show_dev())
            self.nav_buttons["DEV"] = dev
            self.nav_icons["DEV"] = "◈"
            self.nav_short_labels["DEV"] = "DEV"

    def _schedule_responsive_layout(self, event):
        if event.widget is not self:
            return
        if self._responsive_job is not None:
            self.after_cancel(self._responsive_job)
        self._responsive_job = self.after(35, self._apply_responsive_layout)

    def _apply_responsive_layout(self):
        self._responsive_job = None
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        if width <= 1 or height <= 1:
            try:
                dimensions = self.geometry().split("+", 1)[0]
                geometry_width, geometry_height = dimensions.split("x", 1)
                width = max(width, int(geometry_width))
                height = max(height, int(geometry_height))
            except (TypeError, ValueError):
                pass
        mode = "tiny" if width < 900 else ("compact" if width < 1220 else "full")
        short = height < 800

        top_height = 58 if short or mode == "tiny" else 66
        self.top.configure(height=top_height, padx=10 if mode == "tiny" else 18, pady=7 if top_height == 58 else 10)
        self.statusbar.configure(height=52 if short else 58, padx=12 if mode == "tiny" else 18)
        self.sidebar.configure(width=70 if mode == "tiny" else 86, padx=5 if mode == "tiny" else 8, pady=8 if short else 14)

        if mode == "full":
            if not self.brand_name.winfo_manager():
                self.brand_name.pack(side="left", padx=(12, 0))
                self.brand_hub.pack(side="left", padx=(5, 0))
            if not self.search_box.winfo_manager():
                self.search_box.pack(side="left", padx=(38, 12), after=self.brand)
            if not self.quick_button.winfo_manager():
                self.quick_button.pack(side="left", padx=(0, 10), before=self.account_button)
            if not self.guard_button.winfo_manager():
                self.guard_button.pack(side="left", padx=(0, 10), before=self.quick_button)
            self.guard_button.set_layout("Захист: LIVE" if self.guard_enabled else "Захист: ВИМК", 126, 42)
            self.quick_button.set_layout("Швидке встановлення", 165, 42)
            self.account_button.set_layout(self.account_button_text(), 125, 42)
            self.play_button.set_layout("▶  Грати в UKRAINE GTA", 230, 42)
        elif mode == "compact":
            if not self.brand_name.winfo_manager():
                self.brand_name.pack(side="left", padx=(10, 0))
                self.brand_hub.pack(side="left", padx=(4, 0))
            self.search_box.pack_forget()
            self.guard_button.pack_forget()
            if not self.quick_button.winfo_manager():
                self.quick_button.pack(side="left", padx=(0, 8), before=self.account_button)
            self.quick_button.set_layout("Швидко", 96, 40)
            self.account_button.set_layout(self.account_button_text(compact=True), 108, 40)
            self.play_button.set_layout("▶  Грати в UKRAINE GTA", 210, 40)
        else:
            self.brand_name.pack_forget()
            self.brand_hub.pack_forget()
            self.search_box.pack_forget()
            self.guard_button.pack_forget()
            self.quick_button.pack_forget()
            self.account_button.set_layout("●", 42, 40)
            self.play_button.set_layout("▶  Грати", 120, 40)

        for name, button in self.nav_buttons.items():
            icon = self.nav_icons.get(name, "•")
            label = self.nav_short_labels.get(name, name)
            button.configure(
                text=icon if mode == "tiny" else f"{icon}\n{label}",
                pady=5 if short else 7,
                font=("Segoe UI Semibold", 8),
                justify="center",
                anchor="center",
            )
            button.pack_configure(pady=1 if short else 2)
        self.nav_separator.pack_configure(pady=5 if short else 8)
        self._layout_mode = mode

    def page_padding(self):
        width = self.winfo_width()
        return 16 if width < 1000 else (24 if width < 1400 else 36)

    def _clear_content(self):
        for child in self.content_host.winfo_children():
            child.destroy()
        self.cards.clear()

    def _set_active(self, name):
        self.active_category = name
        for key, widget in self.nav_buttons.items():
            active = key == name
            widget.configure(bg="#1a2330" if active else RAIL_BG, fg=ACCENT if active else MUTED)

    def navigate(self, name):
        self.search_var.set("")
        if name == "Головна":
            self.show_home()
        elif name == "Історія":
            self.show_history()
        else:
            self.show_library(name)

    def account_button_text(self, compact=False):
        name = str((self.account_user or {}).get("display_name", "")).strip()
        if not name:
            return "Увійти" if compact else "Профіль"
        short = name if len(name) <= 13 else name[:12] + "…"
        return short if compact else "●  " + short

    def refresh_account_button(self):
        if not hasattr(self, "account_button"):
            return
        mode = self._layout_mode or "full"
        if mode == "tiny":
            self.account_button.set_layout("●", 42, 40)
        elif mode == "compact":
            self.account_button.set_layout(self.account_button_text(compact=True), 110, 42)
        else:
            self.account_button.set_layout(self.account_button_text(), 135, 42)

    def open_account_dialog(self, required=False):
        try:
            if self.account_dialog is not None and self.account_dialog.winfo_exists():
                self.account_dialog.lift()
                self.account_dialog.focus_force()
                return
        except tk.TclError:
            self.account_dialog = None
        self.account_dialog = AccountDialog(self, required=required)

    def ensure_account_session(self):
        protected = str(self.settings.get("account_token", ""))
        token = unprotect_local_secret(protected)
        if not token:
            clear_fastman_access(clear_cache=True)
            self.account_user = {}
            self.settings["account_user"] = {}
            save_settings(self.settings)
            self.refresh_account_button()
            self.open_account_dialog(required=True)
            return
        configure_fastman_access(self.account_url, token)
        self.account_button.text = "Перевірка…"
        self.account_button.draw(False)

        def worker():
            try:
                result = load_account(self.account_url, token)
                self.after(0, lambda: self._account_session_ready(result.get("user") or {}))
            except Exception:
                self.after(0, self._account_session_invalid)

        threading.Thread(target=worker, name="account-session", daemon=True).start()

    def _account_session_ready(self, user):
        self.account_user = dict(user)
        self.settings["account_user"] = self.account_user
        save_settings(self.settings)
        self.refresh_account_button()

    def _account_session_invalid(self):
        clear_fastman_access(clear_cache=True)
        self.settings["account_token"] = ""
        self.settings["account_user"] = {}
        self.account_user = {}
        save_settings(self.settings)
        self.refresh_account_button()
        self.open_account_dialog(required=True)

    def set_account_session(self, token, user):
        if not token or not user:
            raise ModError("Сервер не повернув дані профілю")
        self.account_user = dict(user)
        configure_fastman_access(self.account_url, token)
        self.settings["account_token"] = protect_local_secret(token)
        self.settings["account_user"] = self.account_user
        save_settings(self.settings)
        self.refresh_account_button()
        self.set_status(f"Вхід виконано: {self.account_user.get('display_name', '')}")

    def clear_account_session(self, revoke=True):
        token = unprotect_local_secret(str(self.settings.get("account_token", "")))
        clear_fastman_access(clear_cache=True)
        self.account_user = {}
        self.settings["account_token"] = ""
        self.settings["account_user"] = {}
        save_settings(self.settings)
        self.refresh_account_button()
        if revoke and token:
            threading.Thread(
                target=lambda: self._revoke_account_quietly(token),
                name="account-logout",
                daemon=True,
            ).start()

    def _revoke_account_quietly(self, token):
        try:
            logout_account(self.account_url, token)
        except Exception:
            pass

    def on_search(self, _event=None):
        if _event is not None and getattr(_event, "keysym", "") == "Escape":
            return
        self.show_library("Пошук", self.search_var.get())

    def clear_search(self):
        self.search_var.set("")
        self.show_home()

    def first_run(self):
        if getattr(sys, "frozen", False) and not self.settings.get("ug_brand_migrated", False):
            try:
                if autostart_enabled():
                    set_autostart(True)
                self.settings["ug_brand_migrated"] = True
                save_settings(self.settings)
            except ModError:
                pass
        ok, _ = validate_game_root(self.settings.get("game_root", ""))
        if not ok:
            candidates = detect_game_candidates()
            if candidates:
                self.settings["game_root"] = str(candidates[0])
                save_settings(self.settings)
                self.update_root_status()
        ok, _ = validate_game_root(self.settings.get("game_root", ""))
        if ok:
            try:
                self.show_home()
            except (ModError, OSError):
                pass

    def card_image(self, accent: str, title: str, size=(328, 166)):
        key = f"{accent}:{title}:{size[0]}:{size[1]}"
        if key in self._images:
            return self._images[key]
        width, height = size
        image = Image.new("RGB", size, "#070b12")
        pixels = image.load()
        a = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5))
        seed = sum((index + 1) * ord(char) for index, char in enumerate(title))
        glow_x = width * (0.25 + (seed % 55) / 100)
        glow_y = height * (0.18 + (seed % 21) / 100)
        for y in range(height):
            for x in range(width):
                glow = max(0, 1 - math.dist((x, y), (glow_x, glow_y)) / (width * .72))
                horizon = max(0, 1 - abs(y - height * .62) / (height * .55))
                amount = min(.62, glow * .48 + horizon * .08)
                vignette = max(0.52, 1 - math.dist((x, y), (width / 2, height / 2)) / width * .75)
                pixels[x, y] = tuple(int((8 + (channel - 8) * amount) * vignette) for channel in a)
        draw = ImageDraw.Draw(image, "RGBA")
        for layer in range(3):
            points = []
            base = height * (.58 + layer * .12)
            for point_x in range(-10, width + 20, 8):
                point_y = base + math.sin((point_x + seed * (layer + 1)) / (25 + layer * 8)) * (9 + layer * 5)
                point_y += math.sin(point_x / 11 + layer) * 3
                points.append((point_x, point_y))
            points.extend([(width + 20, height + 10), (-10, height + 10)])
            shade = (5 + layer * 3, 9 + layer * 4, 15 + layer * 5, 205 + layer * 15)
            draw.polygon(points, fill=shade)
            draw.line(points[:-2], fill=(*a, 48 - layer * 9), width=2)
        for index in range(16):
            x = (seed * (index + 3) * 17) % width
            y = (seed * (index + 5) * 11) % max(1, int(height * .56))
            radius = 1 if index % 3 else 2
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*a, 65))
        draw.rectangle((0, height - 34, width, height), fill=(5, 8, 13, 90))
        photo = ImageTk.PhotoImage(image)
        self._images[key] = photo
        return photo

    def mod_image(self, mod: Mod, size=(328, 166)):
        custom = cover_path(mod)
        source = custom
        if source is None and mod.category == "Кров + звук влучання":
            nightline_cover = RESOURCE_DIR / "assets" / "nightline" / "hit-confirm.webp"
            source = nightline_cover if nightline_cover.is_file() else None
        if source is None:
            filename = CATEGORY_COVER_FILES.get(mod.category, CATEGORY_COVER_FILES["Інше"])
            candidate = RESOURCE_DIR / "assets" / "category_covers" / filename
            source = candidate if candidate.is_file() else None
        if source is None:
            return self.card_image(mod.accent, mod.title, size)
        try:
            stamp = source.stat().st_mtime_ns
            key = f"cover:{source}:{stamp}:{size[0]}:{size[1]}"
            if key not in self._images:
                image = Image.open(source).convert("RGB")
                image = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
                overlay = Image.new("RGBA", size, (0, 0, 0, 0))
                ImageDraw.Draw(overlay).rectangle((0, size[1] - 48, size[0], size[1]), fill=(4, 8, 13, 105))
                image = Image.alpha_composite(image.convert("RGBA"), overlay)
                self._images[key] = ImageTk.PhotoImage(image)
            return self._images[key]
        except OSError:
            return self.card_image(mod.accent, mod.title, size)

    def hero_image(self, size=(1500, 270)):
        key = f"hero:{size[0]}:{size[1]}"
        if key in self._images:
            return self._images[key]
        width, height = size
        image = Image.new("RGBA", size, "#0b1017")
        draw = ImageDraw.Draw(image, "RGBA")
        for x in range(width):
            ratio = x / max(1, width - 1)
            draw.line((x, 0, x, height), fill=(12 + int(8 * ratio), 17 + int(12 * ratio), 25 + int(18 * ratio), 255))
        for x in range(0, width, 44):
            draw.line((x, 0, x, height), fill=(102, 199, 255, 13), width=1)
        for y in range(0, height, 44):
            draw.line((0, y, width, y), fill=(102, 199, 255, 10), width=1)

        tram_path = RESOURCE_DIR / "assets" / "nightline" / "tram-decor.webp"
        if tram_path.is_file():
            try:
                tram = Image.open(tram_path).convert("RGBA")
                tram.thumbnail((round(width * .62), round(height * 1.45)), Image.Resampling.LANCZOS)
                tram.putalpha(tram.getchannel("A").point(lambda value: min(255, round(value * 2.4))))
                image.alpha_composite(tram, (width - tram.width - 30, (height - tram.height) // 2))
            except OSError:
                pass
        character_path = RESOURCE_DIR / "assets" / "nightline" / "character.webp"
        if character_path.is_file():
            try:
                character = Image.open(character_path).convert("RGBA")
                character.thumbnail((round(width * .28), round(height * 1.45)), Image.Resampling.LANCZOS)
                image.alpha_composite(character, (round(width * .72), height - character.height + 30))
            except OSError:
                pass

        shade = Image.new("RGBA", size, (0, 0, 0, 0))
        shade_draw = ImageDraw.Draw(shade, "RGBA")
        for x in range(width):
            ratio = x / max(1, width - 1)
            alpha = max(0, round(224 * (1 - min(1, ratio / .72))))
            shade_draw.line((x, 0, x, height), fill=(5, 8, 12, alpha))
        shade_draw.rectangle((0, height - 78, width, height), fill=(5, 8, 12, 82))
        image = Image.alpha_composite(image, shade)
        photo = ImageTk.PhotoImage(image.convert("RGB"))
        self._images[key] = photo
        return photo

    def content_wallpaper(self, size):
        """Render the tram artwork as a dark full-page Nightline backdrop."""
        width, height = size
        key = f"content-wallpaper:{width}:{height}"
        if key in self._images:
            return self._images[key]
        image = Image.new("RGBA", size, BG)
        draw = ImageDraw.Draw(image, "RGBA")
        for y in range(height):
            ratio = y / max(1, height - 1)
            draw.line((0, y, width, y), fill=(7 + round(5 * ratio), 10 + round(8 * ratio), 15 + round(12 * ratio), 255))
        for x in range(0, width, 54):
            draw.line((x, 0, x, height), fill=(102, 199, 255, 9))
        for y in range(0, height, 54):
            draw.line((0, y, width, y), fill=(102, 199, 255, 7))

        source = RESOURCE_DIR / "assets" / "nightline" / "tram-wallpaper@2x.webp"
        if not source.is_file():
            source = RESOURCE_DIR / "assets" / "nightline" / "tram-decor.webp"
        if source.is_file():
            try:
                tram = Image.open(source).convert("RGBA")
                target_width = round(width * 1.08)
                target_height = round(height * 1.02)
                tram.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
                alpha = tram.getchannel("A").point(lambda value: min(255, round(value * 4.25)))
                tram.putalpha(alpha)
                x = (width - tram.width) // 2
                y = max(-40, (height - tram.height) // 2)
                image.alpha_composite(tram, (x, y))
            except OSError:
                pass

        shade = Image.new("RGBA", size, (0, 0, 0, 0))
        shade_draw = ImageDraw.Draw(shade, "RGBA")
        shade_draw.rectangle((0, 0, width, height), fill=(3, 6, 10, 58))
        shade_draw.rectangle((0, 0, width, 120), fill=(3, 6, 10, 54))
        image = Image.alpha_composite(image, shade)
        photo = ImageTk.PhotoImage(image.convert("RGB"))
        self._images[key] = photo
        return photo

    def _add_wallpaper_underlay(self, container, body, wallpaper):
        """Keep one wallpaper visually aligned beneath opaque content panels."""
        layer = tk.Label(container, image=wallpaper, bg=BG, borderwidth=0, highlightthickness=0)

        def align(_event=None):
            try:
                x = container.winfo_rootx() - body.winfo_rootx()
                y = container.winfo_rooty() - body.winfo_rooty()
                layer.place(x=-x, y=-y, anchor="nw")
                layer.lower()
            except tk.TclError:
                pass

        container.bind("<Configure>", align, add="+")
        self.after_idle(align)
        return layer

    def show_home(self):
        self._clear_content()
        self._set_active("Головна")
        scroll = ScrollFrame(self.content_host)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        wallpaper = self.content_wallpaper((max(960, self.content_host.winfo_width()), max(1100, self.content_host.winfo_height() + 320)))
        self._add_wallpaper_underlay(body, body, wallpaper)
        hero = tk.Frame(body, bg=PANEL, height=318, highlightbackground="#283747", highlightthickness=1)
        page_pad = self.page_padding()
        hero.pack(fill="x", padx=page_pad, pady=(20 if page_pad < 30 else 26, 14))
        hero.pack_propagate(False)
        visual = AnimatedHero(hero, self)
        visual.place(relx=0, rely=0, relwidth=1, relheight=1)
        copy = tk.Frame(hero, bg="#0c1219", padx=27, pady=22, highlightbackground="#293744", highlightthickness=1)
        copy.place(x=28, y=24, width=620, height=270)
        hero.bind("<Configure>", lambda event: copy.place_configure(width=max(320, min(620, event.width - 56))))
        tk.Label(copy, text="КОЛЕКЦІЯ  ·  DARK CITY", bg="#0c1219", fg=BLUE, font=("Segoe UI Semibold", 8)).pack(anchor="w")
        tk.Label(copy, text="ЗМІНЮЙ ГРУ.", bg="#0c1219", fg=TEXT, font=("Segoe UI Semibold", 27)).pack(anchor="w", pady=(8, 0))
        tk.Label(copy, text="ЗБЕРІГАЙ СТИЛЬ.", bg="#0c1219", fg=ACCENT, font=("Segoe UI Semibold", 27)).pack(anchor="w")
        tk.Label(copy, text="Перевірені модифікації для UKRAINE GTA з автоматичними\nрезервними копіями та безпечним відновленням.", bg="#0c1219", fg="#a2aab5", font=("Segoe UI", 10), justify="left").pack(anchor="w", pady=(8, 13))
        action_row = tk.Frame(copy, bg="#0c1219")
        action_row.pack(fill="x", anchor="w")
        PillButton(action_row, "Відкрити каталог  →", lambda: self.show_library("Усі моди"), width=185, height=40, primary=False).pack(side="left")
        tk.Label(action_row, text=f"  {len(self.mods)} модів доступно", bg="#0c1219", fg="#6f7884", font=("Segoe UI", 8)).pack(side="left", padx=(10, 0))

        installed_count = len(load_state().get("installed", {}))
        status_strip = tk.Frame(body, bg=BG)
        status_strip.pack(fill="x", padx=page_pad, pady=(0, 20))
        self._add_wallpaper_underlay(status_strip, body, wallpaper)
        status_items = [
            ("ВСТАНОВЛЕНО", str(installed_count), "активні моди", ACCENT),
            ("ДОСТУПНО", str(len([mod for mod in self.mods if mod.category != "Оптимізація"])), "у каталозі", BLUE),
            ("ЗАХИСТ", "LIVE" if self.guard_enabled else "ВИМК", "файли під контролем", GREEN if self.guard_enabled else MUTED),
        ]
        for index, (label, value, detail, color) in enumerate(status_items):
            card = tk.Frame(status_strip, bg=PANEL, padx=16, pady=11, highlightbackground=LINE, highlightthickness=1)
            card.grid(row=0, column=index, sticky="ew", padx=(0, 10 if index < 2 else 0))
            status_strip.grid_columnconfigure(index, weight=1)
            tk.Label(card, text=label, bg=PANEL, fg="#6f7782", font=("Segoe UI Semibold", 7)).pack(anchor="w")
            line = tk.Frame(card, bg=PANEL)
            line.pack(fill="x", pady=(4, 0))
            tk.Label(line, text=value, bg=PANEL, fg=color, font=("Segoe UI Semibold", 15)).pack(side="left")
            tk.Label(line, text=detail, bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack(side="right", pady=(5, 0))

        section = tk.Frame(body, bg=BG)
        section.pack(fill="x", padx=page_pad)
        self._add_wallpaper_underlay(section, body, wallpaper)
        tk.Label(section, text="Популярні моди", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 17)).pack(side="left")
        all_mods = tk.Label(section, text="Показати всі  →", bg=BG, fg=ACCENT, cursor="hand2", font=("Segoe UI Semibold", 9))
        all_mods.pack(side="right")
        all_mods.bind("<Button-1>", lambda _event: self.show_library("Усі моди"))
        grid = ResponsiveCardGrid(body)
        grid.pack(fill="x", padx=page_pad, pady=(15, 30))
        self._add_wallpaper_underlay(grid, body, wallpaper)
        available = [mod for mod in self.mods if mod.category != "Оптимізація"]
        for index, mod in enumerate(available[:4]):
            card = ModCard(grid, self, mod)
            grid.add(card)
            card.animate_intro(index * 70)
            self.cards.append(card)

    def show_library(self, category="Усі моди", search_query=""):
        self._clear_content()
        self._set_active(category if category in self.nav_buttons else "Усі моди")
        self.active_category = category
        scroll = ScrollFrame(self.content_host)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner
        installed = load_state().get("installed", {})
        available = [mod for mod in self.mods if mod.category != "Оптимізація"]
        if category == "Пошук":
            mods = [mod for mod in available if mod_matches_search(mod, search_query)]
        elif category == "Усі моди":
            mods = available
        elif category == "Обране":
            favorites = load_favorites()
            mods = [mod for mod in available if mod.id in favorites]
        elif category == "Встановлено":
            mods = installed_mod_entries(self.mods)
        else:
            mods = [mod for mod in available if mod.category == category]
        header = tk.Frame(body, bg=PANEL, padx=24, pady=20, highlightbackground=LINE, highlightthickness=1)
        page_pad = self.page_padding()
        header.pack(fill="x", padx=page_pad, pady=(22 if page_pad < 30 else 28, 18))
        header_copy = tk.Frame(header, bg=PANEL)
        header_copy.pack(side="left", fill="x", expand=True)
        heading = f"Пошук: {search_query}" if search_query.strip() else ("Пошук модів" if category == "Пошук" else category)
        tk.Label(header_copy, text=heading, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 24)).pack(anchor="w")
        tk.Label(header_copy, text="Пошук за назвою, описом або розділом." if category == "Пошук" else "Обирай мод — оригінальні файли буде збережено перед встановленням.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
        count = tk.Label(header, text=f"{len(mods)}  МОДІВ", bg="#20232b", fg=ACCENT, padx=15, pady=8, font=("Segoe UI Semibold", 8))
        count.pack(side="right")
        if category not in {"Пошук", "Обране", "Встановлено"}:
            filters = tk.Frame(body, bg=BG)
            filters.pack(fill="x", padx=page_pad, pady=(0, 14))
            filter_items = [("Усі моди", "Усі")] + [(item, item) for item in CATEGORIES]
            columns = 4 if self.winfo_width() < 1160 else 6
            for index, (value, label) in enumerate(filter_items):
                active_filter = value == category
                chip = tk.Label(
                    filters,
                    text=label,
                    bg="#242831" if active_filter else PANEL,
                    fg=ACCENT if active_filter else MUTED,
                    padx=10,
                    pady=7,
                    cursor="hand2",
                    font=("Segoe UI Semibold", 8),
                    highlightbackground=ACCENT if active_filter else LINE,
                    highlightthickness=1,
                )
                chip.grid(row=index // columns, column=index % columns, sticky="ew", padx=(0, 6), pady=(0, 6))
                filters.grid_columnconfigure(index % columns, weight=1)
                chip.bind("<Button-1>", lambda _event, selected=value: self.show_library(selected))
        if not mods:
            empty = tk.Frame(body, bg=PANEL, padx=30, pady=50, highlightbackground=LINE, highlightthickness=1)
            empty.pack(fill="x", padx=page_pad, pady=10)
            tk.Label(empty, text="Тут поки порожньо", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 16)).pack()
            tk.Label(empty, text="Додайте файли через DEV або змініть запит пошуку.", bg=PANEL, fg=MUTED, font=("Segoe UI", 10)).pack(pady=(6, 0))
            return
        grid = ResponsiveCardGrid(body)
        grid.pack(fill="both", expand=True, padx=page_pad, pady=(0, 24))
        for index, mod in enumerate(mods):
            card = ModCard(grid, self, mod)
            grid.add(card)
            card.animate_intro(index * 65)
            self.cards.append(card)

    def toggle_favorite(self, mod: Mod):
        favorites = load_favorites()
        enabled = mod.id not in favorites
        set_favorite(mod.id, enabled)
        self.refresh_cards()
        self.set_status(f"«{mod.title}» {'додано до обраного' if enabled else 'видалено з обраного'}")
        if self.active_category == "Обране":
            self.show_library("Обране")

    def show_history(self):
        self._clear_content()
        self._set_active("Історія")
        scroll = ScrollFrame(self.content_host)
        scroll.pack(fill="both", expand=True)
        body = tk.Frame(scroll.inner, bg=BG, padx=36, pady=30)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Історія дій", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 25)).pack(anchor="w")
        tk.Label(body, text="Встановлення, оновлення, відновлення та робота хеш-захисту.", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 20))
        items = load_history()
        if not items:
            tk.Label(body, text="Історія поки порожня", bg=PANEL, fg=MUTED, padx=24, pady=30).pack(fill="x")
            return
        for item in items:
            row = tk.Frame(body, bg=PANEL, padx=18, pady=13, highlightbackground=LINE, highlightthickness=1)
            row.pack(fill="x", pady=3)
            try:
                shown = item.get("time", "").replace("T", " ")[:19]
            except Exception:
                shown = ""
            tk.Label(row, text=shown, bg=PANEL, fg=MUTED, width=20, anchor="w", font=("Segoe UI", 9)).pack(side="left")
            text = item.get("action", "Дія")
            if item.get("title"):
                text += f"  •  {item['title']}"
            tk.Label(row, text=text, bg=PANEL, fg=TEXT, anchor="w", font=("Segoe UI Semibold", 10)).pack(side="left", fill="x", expand=True)
            tk.Label(row, text=item.get("details", ""), bg=PANEL, fg=MUTED, anchor="e", font=("Segoe UI", 9)).pack(side="right")

    def show_dev(self):
        if not self.dev_mode:
            self.show_home()
            return
        self.editing_mod_id = None
        self._clear_content()
        self._set_active("DEV")
        scroll = ScrollFrame(self.content_host)
        scroll.pack(fill="both", expand=True)
        self.dev_scroll = scroll
        body = scroll.inner

        header = tk.Frame(body, bg=BG)
        page_pad = self.page_padding()
        header.pack(fill="x", padx=page_pad, pady=(22 if page_pad < 30 else 28, 18))
        tk.Label(header, text="DEV  •  Керування модами", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 24)).pack(anchor="w")
        tk.Label(header, text="Додавай нові моди або редагуй уже наявні.", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))

        form = tk.Frame(body, bg=PANEL, padx=24, pady=22, highlightbackground="#4b3267", highlightthickness=1)
        form.pack(fill="x", padx=page_pad)

        self.dev_form_title = tk.Label(form, text="Новий мод", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 14))
        self.dev_form_title.pack(anchor="w", pady=(0, 14))
        tk.Label(form, text="1. Папка з модом", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        source_row = tk.Frame(form, bg=PANEL)
        source_row.pack(fill="x", pady=(8, 18))
        self.dev_source_var = tk.StringVar()
        tk.Entry(source_row, textvariable=self.dev_source_var, bg="#090d13", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=11, padx=(0, 10))
        PillButton(source_row, "Вибрати папку…", self.choose_dev_source, width=150, height=42, primary=False).pack(side="right")

        tk.Label(form, text="Обкладинка мода (PNG/JPG)", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w")
        cover_row = tk.Frame(form, bg=PANEL)
        cover_row.pack(fill="x", pady=(7, 18))
        self.dev_cover_var = tk.StringVar()
        tk.Entry(cover_row, textvariable=self.dev_cover_var, bg="#090d13", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=10, padx=(0, 10))
        PillButton(cover_row, "Вибрати зображення…", self.choose_dev_cover, width=175, height=42, primary=False).pack(side="right")

        fields = tk.Frame(form, bg=PANEL)
        fields.pack(fill="x")
        fields.grid_columnconfigure(0, weight=2)
        fields.grid_columnconfigure(1, weight=1)
        fields.grid_columnconfigure(2, weight=2)

        self.dev_title_var = tk.StringVar()
        self.dev_category_var = tk.StringVar(value="Інше")
        self.dev_destination_var = tk.StringVar(value="game")
        title_box = self._dev_field(fields, "2. Назва", self.dev_title_var, 0)

        category_box = tk.Frame(fields, bg=PANEL)
        category_box.grid(row=0, column=1, sticky="ew", padx=8)
        tk.Label(category_box, text="3. Розділ", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(0, 7))
        category = ttk.Combobox(category_box, textvariable=self.dev_category_var, values=CATEGORIES, state="readonly", style="Dark.TCombobox")
        category.pack(fill="x", ipady=7)

        destination_box = tk.Frame(fields, bg=PANEL)
        destination_box.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        tk.Label(destination_box, text="4. Куди встановлювати", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(0, 7))
        destination = ttk.Combobox(destination_box, textvariable=self.dev_destination_var, values=[
            "game/bin/models", "game/bin/data", "game/bin/anim", "game/bin/audio",
            "game/mods/deathmatch/resources", "game/mods/deathmatch/resources/ugta_game_business/Files", "game",
        ], state="normal", style="Dark.TCombobox")
        destination.pack(fill="x", ipady=7)

        def arrange_dev_fields(event):
            narrow = event.width < 800
            for column in range(3):
                fields.grid_columnconfigure(column, weight=0)
            if narrow:
                fields.grid_columnconfigure(0, weight=1)
                fields.grid_columnconfigure(1, weight=1)
                title_box.grid_configure(row=0, column=0, columnspan=2, padx=0, pady=(0, 12))
                category_box.grid_configure(row=1, column=0, padx=(0, 8), pady=0)
                destination_box.grid_configure(row=1, column=1, padx=(8, 0), pady=0)
            else:
                fields.grid_columnconfigure(0, weight=2)
                fields.grid_columnconfigure(1, weight=1)
                fields.grid_columnconfigure(2, weight=2)
                title_box.grid_configure(row=0, column=0, columnspan=1, padx=(0, 8), pady=0)
                category_box.grid_configure(row=0, column=1, padx=8, pady=0)
                destination_box.grid_configure(row=0, column=2, padx=(8, 0), pady=0)

        fields.bind("<Configure>", arrange_dev_fields)

        tk.Label(form, text="Опис (необов’язково)", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(18, 7))
        self.dev_description_var = tk.StringVar(value="Користувацький мод")
        tk.Entry(form, textvariable=self.dev_description_var, bg="#090d13", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10)).pack(fill="x", ipady=11)

        options = tk.Frame(form, bg=PANEL)
        options.pack(fill="x", pady=(15, 18))
        self.dev_repeat_var = tk.BooleanVar(value=False)
        self.dev_crosshair_var = tk.BooleanVar(value=False)
        self.dev_skip_preview_var = tk.BooleanVar(value=True)
        option_buttons = []
        for text, variable in [
            ("Повторювати перед запуском", self.dev_repeat_var),
            ("Це варіант прицілу", self.dev_crosshair_var),
            ("Не копіювати відеопрев’ю", self.dev_skip_preview_var),
        ]:
            button = tk.Checkbutton(options, text=text, variable=variable, bg=PANEL, fg=MUTED, activebackground=PANEL, activeforeground=TEXT, selectcolor="#171f2a", font=("Segoe UI", 9))
            button.pack(side="left", padx=(0, 22))
            option_buttons.append(button)

        def arrange_options(event):
            vertical = event.width < 720
            for button in option_buttons:
                button.pack_forget()
                button.pack(side="top" if vertical else "left", anchor="w", padx=(0, 22), pady=2 if vertical else 0)

        options.bind("<Configure>", arrange_options)

        action_row = tk.Frame(form, bg=PANEL)
        action_row.pack(fill="x")
        self.dev_submit_button = PillButton(action_row, "＋  Додати в бібліотеку", self.create_dev_mod, width=230, height=46)
        self.dev_submit_button.pack(side="left")
        self.dev_cancel_button = PillButton(action_row, "Скасувати редагування", self.cancel_dev_edit, width=190, height=46, primary=False)
        self.dev_hint = tk.Label(action_row, text="Файли зберігатимуться окремо від програми", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.dev_hint.pack(side="left", padx=14)

        export_box = tk.Frame(body, bg="#14101c", padx=24, pady=20, highlightbackground="#6e45a2", highlightthickness=1)
        export_box.pack(fill="x", padx=page_pad, pady=(20, 0))
        tk.Label(export_box, text="Експорт закритого релізу", bg="#14101c", fg=TEXT, font=("Segoe UI Semibold", 14)).pack(anchor="w")
        tk.Label(export_box, text="Створює окрему версійну папку без DEV і без кнопок редагування файлів.", bg="#14101c", fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 12))
        export_row = tk.Frame(export_box, bg="#14101c")
        export_row.pack(fill="x")
        self.export_version_var = tk.StringVar(value=str(self.catalog.get("version", "1.2")))
        tk.Label(export_row, text="Версія", bg="#14101c", fg=MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))
        tk.Entry(export_row, textvariable=self.export_version_var, width=10, bg="#090d13", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10)).pack(side="left", ipady=9, padx=(0, 12))
        PillButton(export_row, "Експортувати реліз без DEV", self.export_release, width=245, height=42).pack(side="left")
        self.export_hint = tk.Label(export_row, text=f"Папка: {APP_DIR.parent}", bg="#14101c", fg=MUTED, font=("Segoe UI", 9))
        self.export_hint.pack(side="left", padx=14, fill="x", expand=True)

        added = [mod for mod in self.mods if payload_files(mod.id)]
        built_in_ids = {item.get("id") for item in self.catalog.get("mods", [])}
        manage = tk.Frame(body, bg=BG)
        manage.pack(fill="x", padx=page_pad, pady=(28, 35))
        tk.Label(manage, text=f"Додані моди  •  {len(added)}", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 16)).pack(anchor="w", pady=(0, 10))
        if not added:
            tk.Label(manage, text="Поки немає модів із файлами", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=14)
        for mod in added:
            row = tk.Frame(manage, bg=PANEL, padx=16, pady=12, highlightbackground=LINE, highlightthickness=1)
            row.pack(fill="x", pady=4)
            copy = tk.Frame(row, bg=PANEL)
            copy.pack(side="left", fill="x", expand=True)
            tk.Label(copy, text=mod.title, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w")
            tk.Label(copy, text=f"{mod.category}  →  {mod.destination}  •  файлів: {len(payload_files(mod.id))}", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))
            if mod.user_defined and mod.id not in built_in_ids:
                PillButton(row, "Видалити", lambda item=mod: self.delete_dev_mod(item), width=90, height=36, primary=False, danger=True).pack(side="right", padx=(8, 0))
            PillButton(row, "Файли", lambda item=mod: self.open_payload(item), width=80, height=36, primary=False).pack(side="right", padx=(8, 0))
            PillButton(row, "Редагувати", lambda item=mod: self.edit_dev_mod(item), width=110, height=36, primary=False).pack(side="right")

    def _dev_field(self, parent, label, variable, column):
        box = tk.Frame(parent, bg=PANEL)
        box.grid(row=0, column=column, sticky="ew", padx=(0, 8))
        tk.Label(box, text=label, bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(0, 7))
        tk.Entry(box, textvariable=variable, bg="#090d13", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10)).pack(fill="x", ipady=10)
        return box

    def edit_dev_mod(self, mod: Mod):
        self.editing_mod_id = mod.id
        self.dev_form_title.configure(text=f"Редагування: {mod.title}")
        self.dev_source_var.set("")
        existing_cover = cover_path(mod)
        self.dev_cover_var.set(str(existing_cover) if existing_cover else "")
        self.dev_title_var.set(mod.title)
        self.dev_category_var.set(mod.category)
        self.dev_destination_var.set(mod.destination)
        self.dev_description_var.set(mod.description)
        self.dev_repeat_var.set(mod.repeat_before_launch)
        self.dev_crosshair_var.set(mod.exclusive_group == "crosshair")
        self.dev_skip_preview_var.set(True)
        self.dev_submit_button.text = "Зберегти зміни"
        self.dev_submit_button.draw(False)
        if not self.dev_cancel_button.winfo_manager():
            self.dev_cancel_button.pack(side="left", padx=(10, 0), before=self.dev_hint)
        self.dev_hint.configure(text="Папку можна не вибирати — поточні файли збережуться", fg=ACCENT)
        self.dev_scroll.canvas.yview_moveto(0)

    def cancel_dev_edit(self):
        self.show_dev()

    def choose_dev_source(self):
        selected = filedialog.askdirectory(title="Виберіть папку з файлами мода")
        if not selected:
            return
        self.dev_source_var.set(selected)
        if self.editing_mod_id:
            self.dev_hint.configure(text="Після збереження поточні файли буде замінено вибраною папкою", fg="#e4a853")
            return
        self.dev_title_var.set(Path(selected).name)
        self.auto_detect_dev_package(Path(selected))

    def choose_dev_cover(self):
        selected = filedialog.askopenfilename(
            title="Виберіть обкладинку мода",
            filetypes=[("Зображення", "*.png *.jpg *.jpeg"), ("Усі файли", "*.*")],
        )
        if selected:
            self.dev_cover_var.set(selected)

    def auto_detect_dev_package(self, folder: Path):
        files = [item for item in folder.rglob("*") if item.is_file()]
        names = {item.name.lower() for item in files}
        extensions = {item.suffix.lower() for item in files}
        folder_name = folder.name.lower()
        category, destination = "Інше", "game"
        description = "Користувацький мод"
        repeat = False
        crosshair = False

        normalized_folder = folder.as_posix().lower()
        if "blood" in folder_name or "кров" in folder_name:
            category = "Кров + звук влучання"
            destination = "game/mods/deathmatch/resources/ugta_game_business/Files"
            description, repeat = "Кров та звук підтвердження влучання", True
        elif {"effects.fxp", "effectspc.txd"} & names:
            category, destination = "Ефекти", "game/bin/models"
            description = "Ефекти зброї та пострілів"
        elif ".ifp" in extensions:
            category, destination = "Анімації", "game/bin/anim"
            description = "Користувацькі анімації"
        elif extensions & {".wav", ".mp3", ".ogg", ".bank"} or "sound" in folder_name or "звук" in folder_name:
            category, destination = "Звуки пострілів", "game/bin/audio"
            description = "Пакет ігрових звуків"
        elif "timecyc.dat" in names or "sky" in folder_name or "небо" in folder_name:
            category, destination = "Небо", "game/bin/data"
            description = "Небо та налаштування атмосфери"
        elif "hud" in folder_name:
            category, destination = "HUD", "game/mods/deathmatch/resources"
            description, repeat = "Користувацький HUD", True
        elif "crosshair" in folder_name or "прицел" in folder_name:
            category, destination = "Приціл", "game/mods/deathmatch/resources"
            description, repeat, crosshair = "Користувацький приціл", True, True
        elif extensions & {".txd", ".dff", ".fxp"}:
            category, destination = "Ефекти", "game/bin/models"
            description = "Графічний мод"

        self.dev_category_var.set(category)
        self.dev_destination_var.set(destination)
        self.dev_description_var.set(description)
        self.dev_repeat_var.set(repeat)
        self.dev_crosshair_var.set(crosshair)
        game_files = len([item for item in files if item.suffix.lower() not in {".mp4", ".mov", ".avi", ".mkv", ".webm"}])
        self.dev_hint.configure(text=f"Знайдено ігрових файлів: {game_files}  •  шлях визначено автоматично", fg=ACCENT)

    def create_dev_mod(self):
        if self.busy:
            return
        source_text = self.dev_source_var.get().strip()
        if not self.editing_mod_id and not source_text:
            messagebox.showerror("Папка з модом", "Спочатку виберіть папку з файлами мода.")
            return
        source_path = Path(source_text).expanduser() if source_text else None
        if source_path and source_path.name.lower() == "resources":
            messagebox.showerror("Виберіть папку конкретного мода", "Не можна додавати всю папку deathmatch/resources. Виберіть у ній лише папку крові, прицілу або HUD.")
            return
        values = {
            "title": self.dev_title_var.get(),
            "category": self.dev_category_var.get(),
            "destination": self.dev_destination_var.get(),
            "source_folder": source_text or None,
            "description": self.dev_description_var.get(),
            "repeat_before_launch": self.dev_repeat_var.get(),
            "exclusive_group": "crosshair" if self.dev_crosshair_var.get() else None,
            "skip_previews": self.dev_skip_preview_var.get(),
            "cover_file": self.dev_cover_var.get().strip() or None,
        }
        editing_mod_id = self.editing_mod_id
        self.busy = True
        action = "збереження змін" if editing_mod_id else "копіювання файлів"
        self.dev_hint.configure(text=f"DEV: {action}…", fg="#e4a853")
        self.set_status(f"DEV: {action}…", 30, 100)

        def worker():
            try:
                if editing_mod_id:
                    mod = update_dev_mod(mod_id=editing_mod_id, **values)
                else:
                    mod = add_custom_mod(**values)
                self.after(0, lambda: complete(mod))
            except (ModError, OSError) as exc:
                self.after(0, lambda error=exc: failed(error))

        def complete(mod):
            self.busy = False
            self.catalog, self.mods = load_catalog()
            verb = "оновлено" if editing_mod_id else "додано"
            self.set_status(f"«{mod.title}» {verb}")
            messagebox.showinfo("DEV", f"«{mod.title}» {verb}.\n\nФайлів: {len(payload_files(mod.id))}\nШлях: {mod.destination}")
            self.show_dev()

        def failed(error):
            self.busy = False
            self.set_status("Помилка збереження мода")
            self.dev_hint.configure(text="Перевірте папку та шлях встановлення", fg=RED)
            messagebox.showerror("Не вдалося зберегти мод", str(error))

        threading.Thread(target=worker, name="dev-import", daemon=True).start()

    def delete_dev_mod(self, mod: Mod):
        if not messagebox.askyesno("Видалити користувацький мод", f"Видалити «{mod.title}» із бібліотеки та його збережені файли?"):
            return
        try:
            delete_custom_mod(mod.id)
        except ModError as exc:
            messagebox.showerror("Не вдалося видалити", str(exc))
            return
        self.catalog, self.mods = load_catalog()
        self.show_dev()

    def export_release(self):
        if self.busy:
            return
        version = self.export_version_var.get().strip()
        self.busy = True
        self.export_hint.configure(text="Експорт…", fg="#e4a853")
        self.set_status("DEV: експорт закритого релізу…", 40, 100)

        def worker():
            try:
                target = export_locked_release(version)
                self.after(0, lambda: complete(target))
            except (ModError, OSError) as exc:
                self.after(0, lambda error=exc: failed(error))

        def complete(target):
            self.busy = False
            self.set_status(f"Реліз готовий: {target.name}")
            self.export_hint.configure(text=str(target), fg=ACCENT)
            messagebox.showinfo("Реліз готовий", f"Створено закриту збірку:\n{target}\n\nDEV і кнопки редагування вимкнено.")

        def failed(error):
            self.busy = False
            self.set_status("Помилка експорту релізу")
            self.export_hint.configure(text="Не вдалося експортувати", fg=RED)
            messagebox.showerror("Експорт релізу", str(error))

        threading.Thread(target=worker, name="release-export", daemon=True).start()

    def show_settings(self):
        self._clear_content()
        self._set_active("Налаштування")
        scroll = ScrollFrame(self.content_host)
        scroll.pack(fill="both", expand=True)
        body = tk.Frame(scroll.inner, bg=BG, padx=self.page_padding(), pady=24 if self.winfo_height() < 800 else 34)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Налаштування", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 24)).pack(anchor="w")
        tk.Label(body, text="Папка гри та параметри запуску", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 25))
        version_card = tk.Frame(body, bg=PANEL, padx=24, pady=18, highlightbackground=LINE, highlightthickness=1)
        version_card.pack(fill="x", pady=(0, 18))
        tk.Label(version_card, text="Версія програми", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(version_card, text=str(self.catalog.get("version", "—")), bg=PANEL, fg=ACCENT, font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(5, 0))
        card = tk.Frame(body, bg=PANEL, padx=24, pady=22, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x")
        tk.Label(card, text="Папка UKRAINEGTA", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(card, text="Можна вибрати кореневу папку UKRAINEGTA або вкладену папку game.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 10))
        row = tk.Frame(card, bg=PANEL)
        row.pack(fill="x")
        self.game_root_var = tk.StringVar(value=self.settings.get("game_root", ""))
        entry = tk.Entry(row, textvariable=self.game_root_var, bg="#090d13", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10))
        entry.pack(side="left", fill="x", expand=True, ipady=11, padx=(0, 10))
        PillButton(row, "Огляд…", self.choose_game_root, width=110, height=42, primary=False).pack(side="right")
        self.path_validation = tk.Label(card, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.path_validation.pack(anchor="w", pady=(10, 0))
        self.validate_settings_path()

        launch_card = tk.Frame(body, bg=PANEL, padx=24, pady=22, highlightbackground=LINE, highlightthickness=1)
        launch_card.pack(fill="x", pady=18)
        tk.Label(launch_card, text="Файл запуску", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(launch_card, text="Необов’язково: програма спробує знайти .exe автоматично.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 10))
        row2 = tk.Frame(launch_card, bg=PANEL)
        row2.pack(fill="x")
        self.game_exe_var = tk.StringVar(value=self.settings.get("game_exe", ""))
        tk.Entry(row2, textvariable=self.game_exe_var, bg="#090d13", fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=11, padx=(0, 10))
        PillButton(row2, "Вибрати…", self.choose_game_exe, width=110, height=42, primary=False).pack(side="right")

        server_card = tk.Frame(body, bg=PANEL, padx=24, pady=18, highlightbackground=LINE, highlightthickness=1)
        server_card.pack(fill="x", pady=(0, 18))
        tk.Label(server_card, text="Автопідключення до сервера", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(server_card, text="Запускається офіційний UKRAINE GTA.exe, після чого MTA підключається до вибраного сервера.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 10))
        current_host = str(self.settings.get("server_host", ""))
        try:
            current_port = int(self.settings.get("server_port", 22003))
        except (TypeError, ValueError):
            current_port = 22003
        servers = available_server_choices(self.game_root_var.get(), current_host, current_port)
        self.server_choice_map = {
            f"{item['name']}  —  {item['host']}:{item['port']}": (item["host"], item["port"])
            for item in servers
        }
        selected_label = ""
        for label, address in self.server_choice_map.items():
            if address == (current_host, current_port):
                selected_label = label
                break
        if not selected_label and not current_host:
            selected_label = next((label for label in self.server_choice_map if "#05" in label or "#5" in label), "")
        if not selected_label and current_host:
            selected_label = f"Останній сервер  —  {current_host}:{current_port}"
            self.server_choice_map[selected_label] = (current_host, current_port)
        if not selected_label:
            selected_label = "Південна Україна [ #05 ]  —  s5.ukraine-gta.com.ua:22003"
            self.server_choice_map[selected_label] = ("s5.ukraine-gta.com.ua", 22003)
        self.server_var = tk.StringVar(value=selected_label)
        server_box = ttk.Combobox(server_card, textvariable=self.server_var, values=list(self.server_choice_map), state="readonly", style="Dark.TCombobox")
        server_box.pack(fill="x", ipady=7)

        guard_card = tk.Frame(body, bg=PANEL, padx=24, pady=18, highlightbackground=LINE, highlightthickness=1)
        guard_card.pack(fill="x", pady=(0, 18))
        self.settings_guard_var = tk.BooleanVar(value=self.guard_enabled)
        tk.Checkbutton(guard_card, text="Захист ресурсів за SHA-256", variable=self.settings_guard_var, bg=PANEL, fg=TEXT, activebackground=PANEL, activeforeground=TEXT, selectcolor="#171f2a", font=("Segoe UI Semibold", 11)).pack(anchor="w")
        tk.Label(guard_card, text="Постійно перевіряються: Кров + звук влучання, Приціл і HUD. Захист працює навіть під час підключення до сервера та повертає файли, якщо сервер змінив їхній SHA-256.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=900, justify="left").pack(anchor="w", pady=(5, 0))

        autostart_card = tk.Frame(body, bg=PANEL, padx=24, pady=18, highlightbackground=LINE, highlightthickness=1)
        autostart_card.pack(fill="x", pady=(0, 18))
        tk.Label(autostart_card, text="Автозапуск разом із Windows", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(autostart_card, text="UG MOD HUB запускатиметься після входу в Windows з правами адміністратора.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 10))
        self.autostart_button = PillButton(autostart_card, "", self.toggle_autostart, width=230, height=42, primary=False)
        self.autostart_button.pack(anchor="w")
        self.refresh_autostart_button()

        animation_card = tk.Frame(body, bg=PANEL, padx=24, pady=18, highlightbackground=LINE, highlightthickness=1)
        animation_card.pack(fill="x", pady=(0, 18))
        tk.Label(animation_card, text="Анімації інтерфейсу", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(animation_card, text="Спрощений або вимкнений режим зменшує навантаження на слабких комп’ютерах.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 10))
        self.animation_mode_var = tk.StringVar(value=self.animation_mode)
        ttk.Combobox(animation_card, textvariable=self.animation_mode_var, values=["Повні", "Спрощені", "Вимкнені"], state="readonly", style="Dark.TCombobox", width=24).pack(anchor="w", ipady=7)

        fps_card = tk.Frame(body, bg=PANEL, padx=24, pady=18, highlightbackground=LINE, highlightthickness=1)
        fps_card.pack(fill="x", pady=(0, 18))
        self.fps_counter_var = tk.BooleanVar(value=self.fps_counter_enabled)
        tk.Checkbutton(
            fps_card, text="Показувати лічильник FPS у правому нижньому куті",
            variable=self.fps_counter_var, bg=PANEL, fg=TEXT, activebackground=PANEL,
            activeforeground=TEXT, selectcolor="#171f2a", font=("Segoe UI Semibold", 11),
        ).pack(anchor="w")
        tk.Label(
            fps_card,
            text="Реальний FPS вимірюється через Intel PresentMon. Для відображення поверх гри рекомендовано віконний або безрамковий режим.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=900, justify="left",
        ).pack(anchor="w", pady=(5, 0))

        safety_card = tk.Frame(body, bg=PANEL, padx=24, pady=18, highlightbackground=LINE, highlightthickness=1)
        safety_card.pack(fill="x", pady=(0, 18))
        tk.Label(safety_card, text="Безпека гри", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(safety_card, text="Перевірка знаходить неправильну папку, відсутні файли, конфлікти, запущену гру та нестачу місця.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 12))
        safe_actions = tk.Frame(safety_card, bg=PANEL)
        safe_actions.pack(fill="x")
        PillButton(safe_actions, "Перевірити перед запуском", self.run_preflight, width=225, height=42, primary=False).pack(side="left")
        PillButton(safe_actions, "Відновити чисту гру", self.clean_restore, width=195, height=42, primary=False, danger=True).pack(side="left", padx=10)

        online_card = tk.Frame(body, bg=PANEL, padx=24, pady=18, highlightbackground=LINE, highlightthickness=1)
        online_card.pack(fill="x", pady=(0, 18))
        tk.Label(online_card, text="Оновлення", bg=PANEL, fg=TEXT, font=("Segoe UI Semibold", 12)).pack(anchor="w")
        tk.Label(online_card, text="Каталог і програма перевіряються автоматично. Службові адреси та ключі заховано.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 10))
        key_row = tk.Frame(online_card, bg=PANEL)
        key_row.pack(fill="x")
        PillButton(key_row, "Оновити каталог", self.update_catalog, width=165, height=42).pack(side="left")
        PillButton(key_row, "Перевірити програму", lambda: self.check_for_application_update(force=True), width=190, height=42, primary=False).pack(side="left", padx=(10, 0))

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x")
        PillButton(actions, "Зберегти налаштування", self.commit_settings, width=210, height=44).pack(side="left")
        if not is_admin():
            PillButton(actions, "Перезапустити від адміністратора", self.elevate, width=260, height=44, primary=False).pack(side="left", padx=12)

    def choose_game_root(self):
        selected = filedialog.askdirectory(title="Виберіть папку UKRAINEGTA")
        if selected:
            self.game_root_var.set(selected)
            self.validate_settings_path()

    def choose_game_exe(self):
        initial = self.game_root_var.get() or str(Path.home())
        selected = filedialog.askopenfilename(title="Виберіть файл запуску", initialdir=initial, filetypes=[("Програми", "*.exe"), ("Усі файли", "*.*")])
        if selected:
            self.game_exe_var.set(selected)

    def validate_settings_path(self):
        ok, message = validate_game_root(self.game_root_var.get())
        self.path_validation.configure(text=("✓ " if ok else "⚠ ") + message, fg=ACCENT if ok else "#e4a853")

    def commit_settings(self):
        ok, message = validate_game_root(self.game_root_var.get())
        if not ok:
            messagebox.showerror("Неправильна папка", message)
            return
        self.settings["game_root"] = message
        self.settings["game_exe"] = self.game_exe_var.get().strip()
        server = self.server_choice_map.get(self.server_var.get())
        if server:
            self.settings["server_host"], self.settings["server_port"] = server
        self.guard_enabled = bool(self.settings_guard_var.get())
        self.settings["resource_guard"] = self.guard_enabled
        self.animation_mode = self.animation_mode_var.get()
        self.settings["animation_mode"] = self.animation_mode
        self.fps_counter_enabled = bool(self.fps_counter_var.get())
        self.settings["fps_counter"] = self.fps_counter_enabled
        save_settings(self.settings)
        self.update_root_status()
        self.refresh_guard_button()
        self.fps_overlay.set_enabled(self.fps_counter_enabled)
        self.set_status("Налаштування збережено")
        messagebox.showinfo("UG MOD HUB", "Налаштування збережено")

    def update_root_status(self):
        ok, message = validate_game_root(self.settings.get("game_root", ""))
        self._game_root_ok = ok
        self.root_status.configure(text="● UKRAINE GTA готова" if ok else "○ Гру не налаштовано", fg=GREEN if ok else MUTED)
        self.play_button.set_enabled(ok)
        self.quick_button.set_enabled(ok)

    def refresh_guard_button(self):
        if self._layout_mode == "tiny":
            self.guard_button.text = "LIVE" if self.guard_enabled else "ВИМК"
        elif self._layout_mode == "compact":
            self.guard_button.text = "Захист"
        else:
            self.guard_button.text = "Захист: LIVE" if self.guard_enabled else "Захист: ВИМК"
        self.guard_button.primary = self.guard_enabled
        self.guard_button.danger = False
        self.guard_button.draw(False)

    def animate_ambient(self):
        if self._window_minimized:
            try:
                self.after(750, self.animate_ambient)
            except tk.TclError:
                pass
            return
        if self.animation_mode != "Повні":
            try:
                self.logo.itemconfigure(self.logo_shape, fill="#111720", outline="#253443")
                self.after(500, self.animate_ambient)
            except tk.TclError:
                pass
            return
        self.animation_phase += 0.12
        amount = (math.sin(self.animation_phase) + 1) / 2
        glow = blend(BLUE, ACCENT, amount * 0.42)
        try:
            self.logo.itemconfigure(self.logo_shape, fill="#111720", outline=glow)
            self.brand_hub.configure(fg=blend(BLUE, ACCENT, amount * .28))
            if getattr(self, "_game_root_ok", False):
                self.root_status.configure(fg=blend(GREEN, "#b6ffcb", amount * .35))
            self.status_text.configure(fg=blend(MUTED, "#c0c9d4", amount * .22))
            self.after(80, self.animate_ambient)
        except tk.TclError:
            return

    def toggle_resource_guard(self):
        self.guard_enabled = not self.guard_enabled
        self.settings["resource_guard"] = self.guard_enabled
        save_settings(self.settings)
        self.refresh_guard_button()
        self.set_status("Захист ресурсів увімкнено" if self.guard_enabled else "Захист ресурсів вимкнено")

    def refresh_autostart_button(self):
        enabled = autostart_enabled()
        self.autostart_button.text = "Автозапуск Windows: УВІМК" if enabled else "Автозапуск Windows: ВИМК"
        self.autostart_button.primary = enabled
        self.autostart_button.draw(False)

    def toggle_autostart(self):
        try:
            enabled = autostart_enabled()
            set_autostart(not enabled)
            self.refresh_autostart_button()
            self.set_status("Автозапуск Windows увімкнено" if not enabled else "Автозапуск Windows вимкнено")
        except ModError as exc:
            messagebox.showerror("Автозапуск Windows", str(exc))

    def start_guard_worker(self):
        if self.guard_thread and self.guard_thread.is_alive():
            return

        def watch():
            while not self.guard_shutdown.wait(1.0):
                if not self.guard_enabled:
                    continue
                if self.busy:
                    continue
                root = self.settings.get("game_root", "")
                ok, _ = validate_game_root(root)
                if not ok:
                    continue
                try:
                    result = sync_managed_resources(root, self.guard_hashes)
                except (ModError, OSError):
                    continue
                if result["repaired"]:
                    count = result["repaired"]
                    try:
                        self.after(0, lambda value=count: self.set_status(f"Захист ресурсів відновив файлів: {value}"))
                    except tk.TclError:
                        return

        self.guard_thread = threading.Thread(target=watch, name="resource-guard", daemon=True)
        self.guard_thread.start()

    def close_app(self):
        self.guard_shutdown.set()
        self.fps_overlay.close()
        release_single_instance()
        self.destroy()

    def set_status(self, text, value=None, maximum=None):
        self.status_text.configure(text=text)
        if value is None:
            self.progress.pack_forget()
        else:
            self.progress.configure(maximum=maximum or 100, value=value)
            if not self.progress.winfo_ismapped():
                self.progress.pack(side="left", padx=15, pady=11)
        self.update_idletasks()

    def refresh_cards(self):
        for card in self.cards:
            card.refresh()

    def _run_job(self, title, job, success, on_done=None, cancellable=True):
        if self.busy:
            return
        self.busy = True
        self.set_status(title, 0, 100)
        cancel_event = threading.Event()
        dialog = TransferDialog(self, title, cancel_event, cancellable)

        def progress(current, total, filename, bytes_done=0, total_bytes=0):
            self.after(0, lambda: dialog.update_progress(current, total, filename, bytes_done, total_bytes))
            value = bytes_done if total_bytes else current
            maximum = total_bytes if total_bytes else total
            self.after(0, lambda: self.set_status(f"{title}  {filename}", value, max(maximum, 1)))

        def worker():
            try:
                result = job(progress, cancel_event)
                self.after(0, lambda: done(result))
            except Exception as exc:
                self.after(0, lambda err=exc: failed(err))

        def done(result):
            self.busy = False
            if dialog.winfo_exists():
                dialog.grab_release()
                dialog.destroy()
            self.set_status(success)
            self.refresh_cards()
            if on_done:
                on_done(result)

        def failed(error):
            self.busy = False
            if dialog.winfo_exists():
                dialog.grab_release()
                dialog.destroy()
            if isinstance(error, OperationCancelled):
                self.set_status("Операцію безпечно скасовано")
            else:
                self.set_status("Помилка")
                messagebox.showerror("UG MOD HUB", str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _choose_skin_target(self, root: str) -> SkinTarget | None:
        targets = load_skin_targets(root)
        if not targets:
            messagebox.showerror("Скіни", "Не вдалося завантажити список назв та ID скінів.")
            return None
        dialog = SkinTargetDialog(self, targets)
        self.wait_window(dialog)
        return dialog.result

    def _choose_skin_template(self, target: SkinTarget) -> tuple[str, Path] | None:
        found = find_skin_template(target, self.settings)
        if found:
            return found
        selected = filedialog.askopenfilename(
            parent=self,
            title=f"Виберіть відкритий IMG для {target.name} (ID {target.id})",
            filetypes=[("Відкриті IMG", "*.img"), ("Усі файли", "*.*")],
        )
        if not selected:
            return None
        path = Path(selected).resolve()
        try:
            if not img_contains_skin(path, target):
                raise ModError(
                    f"У цьому IMG немає {target.dff_filename} та {target.txd_filename}"
                )
        except ModError as exc:
            messagebox.showerror("Неправильний IMG-шаблон", str(exc))
            return None
        archive_name = "PEDS.img" if "peds" in path.name.casefold() else "gta3.img"
        key = "skin_peds_template" if archive_name == "PEDS.img" else "skin_gta3_template"
        self.settings[key] = str(path)
        save_settings(self.settings)
        return archive_name, path

    def _choose_accessory_target(self, root: str) -> AccessoryTarget | None:
        targets = load_accessory_targets(root)
        if not targets:
            messagebox.showerror("Аксесуари", "Не вдалося завантажити список аксесуарів із Lua та IDE.")
            return None
        dialog = SkinTargetDialog(self, targets, "аксесуар")
        self.wait_window(dialog)
        return dialog.result

    def _choose_weapon_target(self, root: str) -> WeaponTarget | None:
        targets = load_weapon_targets(root)
        if not targets:
            messagebox.showerror("Заміна зброї", "Не вдалося завантажити список зброї.")
            return None
        dialog = SkinTargetDialog(self, targets, "вид зброї")
        self.wait_window(dialog)
        return dialog.result

    def _choose_accessory_template(self, target: AccessoryTarget) -> Path | None:
        found = find_accessory_template(target, self.settings)
        if found:
            return found
        selected = filedialog.askopenfilename(
            parent=self,
            title=f"Виберіть відкритий acs.img для {target.name} (ID {target.id})",
            filetypes=[("Відкритий acs.img", "*.img"), ("Усі файли", "*.*")],
        )
        if not selected:
            return None
        path = Path(selected).resolve()
        try:
            if path.name.casefold() != "acs.img" or not img_contains_skin(path, target):
                raise ModError(
                    f"У цьому acs.img немає {target.dff_filename} та {target.txd_filename}"
                )
        except ModError as exc:
            messagebox.showerror("Неправильний IMG-шаблон", str(exc))
            return None
        self.settings["accessory_acs_template"] = str(path)
        save_settings(self.settings)
        return path

    def install(self, mod: Mod):
        root = self.settings.get("game_root", "")
        ok, _ = validate_game_root(root)
        if not ok:
            messagebox.showwarning("Спочатку налаштування", "Спочатку виберіть папку UKRAINEGTA в налаштуваннях.")
            self.show_settings()
            return
        skin_target = None
        skin_archive = ""
        skin_template = None
        accessory_target = None
        accessory_template = None
        weapon_target = None
        weapon_template = None
        if mod.mod_type == "skin" or mod.category == "Скіни":
            skin_target = self._choose_skin_target(root)
            if skin_target is None:
                return
            # Core finds PEDS.img or gta3.img from the selected target automatically.
            skin_archive = ""
            installed = load_state().get("installed", {})
            conflict_id = next((
                mod_id for mod_id, record in installed.items()
                if record.get("kind") == "skin"
                and int(record.get("target_id", -1)) == skin_target.id
                and mod_id != mod.id
            ), None)
            if conflict_id:
                record = installed[conflict_id]
                if not messagebox.askyesno(
                    "Замінити встановлений скін",
                    f"«{record.get('title', conflict_id)}» уже замінює {skin_target.name}.\n\n"
                    f"Видалити попередню заміну та встановити «{mod.title}»?",
                ):
                    return
                known = {item.id: item for item in installed_mod_entries(self.mods)}
                try:
                    uninstall_mod(known.get(conflict_id) or Mod(conflict_id, record.get("title", conflict_id), "Скіни", "", "game/bin/models", mod_type="skin"), root)
                except ModError as exc:
                    messagebox.showerror("Не вдалося замінити скін", str(exc))
                    return
        if mod.mod_type == "accessory" or mod.category == "Аксесуари":
            accessory_target = self._choose_accessory_target(root)
            if accessory_target is None:
                return
            requested = {
                accessory_target.dff_filename.casefold(),
                accessory_target.txd_filename.casefold(),
            }
            installed = load_state().get("installed", {})
            conflict_ids = []
            for mod_id, record in installed.items():
                if record.get("kind") != "accessory" or mod_id == mod.id:
                    continue
                occupied = {
                    str(record.get("target_dff", "")).casefold(),
                    str(record.get("target_txd", "")).casefold(),
                }
                if requested & occupied:
                    conflict_ids.append(mod_id)
            if conflict_ids:
                titles = ", ".join(installed[item].get("title", item) for item in conflict_ids)
                if not messagebox.askyesno(
                    "Спільна модель або текстура",
                    f"Вибраний аксесуар використовує DFF/TXD, які вже замінюють: {titles}.\n\n"
                    f"Видалити конфліктні заміни та встановити «{mod.title}»?",
                ):
                    return
                known = {item.id: item for item in installed_mod_entries(self.mods)}
                try:
                    for conflict_id in conflict_ids:
                        record = installed[conflict_id]
                        old = known.get(conflict_id) or Mod(
                            conflict_id, record.get("title", conflict_id), "Аксесуари", "",
                            "game/bin/data/maps/ACS", mod_type="accessory",
                        )
                        uninstall_mod(old, root)
                except ModError as exc:
                    messagebox.showerror("Не вдалося усунути конфлікт", str(exc))
                    return
        if mod.mod_type == "weapon" or mod.category == "Заміна зброї":
            weapon_target = self._choose_weapon_target(root)
            if weapon_target is None:
                return
            requested = {
                weapon_target.dff_filename.casefold(),
                weapon_target.txd_filename.casefold(),
            }
            installed = load_state().get("installed", {})
            conflict_ids = []
            for mod_id, record in installed.items():
                if record.get("kind") != "weapon" or mod_id == mod.id:
                    continue
                occupied = {
                    str(record.get("target_dff", "")).casefold(),
                    str(record.get("target_txd", "")).casefold(),
                }
                if requested & occupied:
                    conflict_ids.append(mod_id)
            if conflict_ids:
                titles = ", ".join(installed[item].get("title", item) for item in conflict_ids)
                if not messagebox.askyesno(
                    "Замінити встановлену зброю",
                    f"Вибрану зброю вже замінює: {titles}.\n\n"
                    f"Видалити попередню заміну та встановити «{mod.title}»?",
                ):
                    return
                known = {item.id: item for item in installed_mod_entries(self.mods)}
                try:
                    for conflict_id in conflict_ids:
                        record = installed[conflict_id]
                        old = known.get(conflict_id) or Mod(
                            conflict_id, record.get("title", conflict_id), "Заміна зброї", "",
                            "game/bin/models", mod_type="weapon",
                        )
                        uninstall_mod(old, root)
                except ModError as exc:
                    messagebox.showerror("Не вдалося замінити зброю", str(exc))
                    return
        if mod.exclusive_group:
            state = load_state().get("installed", {})
            conflicts = [item for item in self.mods if item.exclusive_group == mod.exclusive_group and item.id != mod.id and item.id in state]
            for conflict in conflicts:
                if not messagebox.askyesno("Заміна варіанта", f"«{conflict.title}» уже встановлено. Замінити його на «{mod.title}»?"):
                    return
                try:
                    uninstall_mod(conflict, root)
                except ModError as exc:
                    messagebox.showerror("Не вдалося усунути конфлікт", str(exc))
                    return

        try:
            file_conflicts = [] if (skin_target or accessory_target or weapon_target) else installed_file_conflicts(mod, root)
        except ModError as exc:
            messagebox.showerror("Перевірка конфліктів", str(exc))
            return
        if file_conflicts:
            details = "\n".join(
                f"• {item['title']}: {', '.join(Path(name).name for name in item['files'][:4])}"
                for item in file_conflicts
            )
            if not messagebox.askyesno(
                "Замінити встановлений мод",
                "Новий мод використовує ті самі файли:\n\n"
                f"{details}\n\nВидалити старий мод, відновити оригінали та встановити «{mod.title}»?",
            ):
                return
            known = {item.id: item for item in installed_mod_entries(self.mods)}
            try:
                for conflict in file_conflicts:
                    old_mod = known.get(conflict["id"]) or Mod(
                        conflict["id"], conflict["title"], "Інше", "", "game"
                    )
                    uninstall_mod(old_mod, root)
            except ModError as exc:
                messagebox.showerror("Не вдалося замінити мод", str(exc))
                return

        def job(progress, cancel_event):
            return install_mod(
                mod, root, progress, cancel_event,
                skin_target=skin_target,
                skin_archive=skin_archive,
                skin_template=skin_template,
                accessory_target=accessory_target,
                accessory_template=accessory_template,
                weapon_target=weapon_target,
                weapon_template=weapon_template,
            )

        self._run_job(f"Встановлення «{mod.title}»…", job, f"«{mod.title}» встановлено")

    def remove(self, mod: Mod):
        if not messagebox.askyesno("Видалення мода", f"Видалити «{mod.title}» і відновити оригінальні файли?"):
            return
        root = self.settings.get("game_root", "")
        self._run_job(
            f"Відновлення файлів «{mod.title}»…",
            lambda progress, cancel: uninstall_mod(mod, root),
            f"«{mod.title}» видалено",
            on_done=lambda _result: self.show_library("Встановлено") if self.active_category == "Встановлено" else None,
            cancellable=False,
        )

    def open_payload(self, mod: Mod):
        folder = ensure_editable_payload(mod.id) if self.dev_mode else payload_dir(mod.id)
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(folder)])

    def quick_apply(self):
        root = self.settings.get("game_root", "")
        installed = load_state().get("installed", {})
        selected = [
            mod for mod in self.mods
            if mod.repeat_before_launch and mod.id in installed and payload_files(mod.id)
        ]
        if not selected:
            messagebox.showinfo("Швидке встановлення", "Додайте файли для крові, прицілу або HUD — після цього вони з’являться у швидкому встановленні.")
            return

        def job(progress, cancel_event):
            total_bytes = sum(payload_size(mod.id) for mod in selected)
            bytes_before = 0
            for index, mod in enumerate(selected, 1):
                if cancel_event.is_set():
                    raise OperationCancelled("Швидке встановлення скасовано")
                def mod_progress(_current, _total, filename, bytes_done, _total_bytes, base=bytes_before, item=mod):
                    progress(index, len(selected), f"{item.title}  •  {filename}", base + bytes_done, total_bytes)
                install_mod(mod, root, mod_progress, cancel_event)
                bytes_before += payload_size(mod.id)
            return True

        self._run_job("Швидке встановлення…", job, "Моди перед запуском оновлено")

    def _show_preflight(self, issues, title="Перевірка перед запуском"):
        icons = {"ok": "✓", "warning": "⚠", "error": "✕"}
        text = "\n\n".join(
            f"{icons.get(item['severity'], '•')} {item['title']}\n{item['detail']}"
            for item in issues
        )
        if any(item["severity"] == "error" for item in issues):
            messagebox.showerror(title, text)
        elif any(item["severity"] == "warning" for item in issues):
            messagebox.showwarning(title, text)
        else:
            messagebox.showinfo(title, text)

    def run_preflight(self):
        installed = load_state().get("installed", {})
        selected = [mod for mod in self.mods if mod.id in installed]
        self._show_preflight(preflight_check(self.settings.get("game_root", ""), selected))

    def clean_restore(self):
        count = len(load_state().get("installed", {}))
        if not count:
            messagebox.showinfo("Чиста гра", "Встановлених модів немає.")
            return
        if not messagebox.askyesno(
            "Відновити чисту гру",
            f"Видалити всі встановлені моди ({count}) та повернути оригінальні файли з резервних копій?",
        ):
            return
        root = self.settings.get("game_root", "")
        self._run_job(
            "Відновлення чистої гри…",
            lambda progress, cancel: restore_clean_game(root, self.mods, progress, cancel),
            "Оригінальні файли гри відновлено",
        )

    def update_catalog(self):
        self._start_catalog_sync(True)

    def check_for_application_update(self, force=False):
        if self._update_checked and not force:
            return
        self._update_checked = True
        url = self.settings.get("update_url", "").strip()
        key = self.settings.get("catalog_public_key", "").strip()
        if not url or not key:
            if force:
                messagebox.showinfo("Оновлення", "Сервіс оновлень ще не налаштований.")
            return

        def worker():
            try:
                payload = check_application_update(url, key, str(self.catalog.get("version", "0.0")))
                self.after(0, lambda: self._offer_application_update(payload, force))
            except ModError as exc:
                if force:
                    self.after(0, lambda: messagebox.showerror("Оновлення", str(exc)))

        threading.Thread(target=worker, name="application-update-check", daemon=True).start()

    def _offer_application_update(self, payload, force=False):
        if not payload:
            if force:
                messagebox.showinfo("Оновлення", "Установлено актуальну версію UG MOD HUB.")
            return
        notes = str(payload.get("notes", "")).strip()
        message = "Доступне оновлення UG MOD HUB."
        if notes:
            message += f"\n\n{notes}"
        mandatory = bool(payload.get("mandatory"))
        if not mandatory and not messagebox.askyesno("Оновлення UG MOD HUB", message + "\n\nЗавантажити зараз?"):
            return
        if mandatory:
            messagebox.showinfo("Обов’язкове оновлення", message)

        def done(path):
            try:
                launch_application_updater(path)
                release_single_instance()
                self.destroy()
            except ModError as exc:
                messagebox.showerror("Оновлення", str(exc))

        self._run_job(
            "Завантаження оновлення UG MOD HUB",
            lambda progress, cancel: download_application_update(payload, progress, cancel),
            "Оновлення завантажено",
            on_done=done,
        )

    def open_server_selector(self):
        if self.busy:
            return
        ok, message = validate_game_root(self.settings.get("game_root", ""))
        if not ok:
            messagebox.showerror("UKRAINE GTA", message)
            return
        try:
            if self.server_dialog is not None and self.server_dialog.winfo_exists():
                self.server_dialog.lift()
                self.server_dialog.focus_force()
                return
        except tk.TclError:
            self.server_dialog = None
        current_host = str(self.settings.get("server_host") or "s5.ukraine-gta.com.ua")
        try:
            current_port = int(self.settings.get("server_port", 22003))
        except (TypeError, ValueError):
            current_port = 22003
        choices = available_server_choices(self.settings.get("game_root", ""), current_host, current_port)
        self.server_dialog = ServerSelectDialog(
            self,
            choices,
            current_host,
            current_port,
            self._server_selected_for_launch,
        )

    def _server_selected_for_launch(self, server):
        self.server_dialog = None
        self.settings["server_host"] = str(server["host"])
        self.settings["server_port"] = int(server["port"])
        save_settings(self.settings)
        self.set_status(f"Обрано сервер: {server['name']}")
        self.start_game(server)

    def start_game(self, server=None):
        try:
            installed = load_state().get("installed", {})
            selected = [mod for mod in self.mods if mod.id in installed]
            issues = preflight_check(self.settings.get("game_root", ""), selected)
            if any(item["severity"] == "error" for item in issues):
                self._show_preflight(issues, "Гру не запущено")
                return
            root = self.settings.get("game_root", "")
            host = str((server or {}).get("host") or self.settings.get("server_host") or "s5.ukraine-gta.com.ua")
            try:
                port = int((server or {}).get("port") or self.settings.get("server_port", 22003))
            except (TypeError, ValueError):
                port = 22003
            server_name = str((server or {}).get("name") or f"{host}:{port}")

            def launch_after_check(result):
                try:
                    launch_game(root, self.settings.get("game_exe", ""), host, port)
                    self.set_status(
                        f"Перевірено {result['checked']}, відновлено {result['repaired']} • запуск: {server_name}"
                    )
                except ModError as exc:
                    messagebox.showerror("Запуск гри", str(exc))

            self._run_job(
                f"Перевірка файлів перед запуском · {server_name}",
                lambda progress, cancel: verify_installed_files(root, self.mods, progress, cancel),
                "Усі файли перевірено",
                on_done=launch_after_check,
            )
        except ModError as exc:
            messagebox.showerror("Запуск гри", str(exc))

    def elevate(self):
        try:
            release_single_instance()
            restart_as_admin()
            self.destroy()
        except ModError as exc:
            acquire_single_instance()
            messagebox.showerror("UG MOD HUB", str(exc))


def main():
    enable_dpi_awareness()
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("UGModHub.Desktop")
        except Exception:
            pass
    if not acquire_single_instance():
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "UG MOD HUB уже запущено.", "UG MOD HUB", 0x40)
        return
    app = ModHub()
    if "--smoke-test" in sys.argv:
        app.update_idletasks()
        app.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    main()
