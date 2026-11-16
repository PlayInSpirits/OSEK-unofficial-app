"""Desktop shell. The Moodle assignment form remains the original site inside the app."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

import keyring
import webview
from PIL import Image, ImageDraw

from .client import BASE, SDOClient, SDOError, safe_url


SERVICE = "SDO-Reborn"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / name


def icon_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), "#151b30")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((5, 5, 59, 59), radius=15, fill="#595cf2")
    draw.text((17, 19), "S", fill="white", stroke_width=1)
    return image


class Bridge:
    def __init__(self):
        self.client = SDOClient()
        self.window = None
        self.site_window = None
        self.site_target = BASE + "/my/"
        self.site_login_pending = False
        self._lock = threading.RLock()
        self._tray = None
        self._quitting = False
        self._known_notifications: set[str] = set()

    def bootstrap(self):
        username = keyring.get_password(SERVICE, "__last_user__")
        password = keyring.get_password(SERVICE, username) if username else None
        if username and password:
            try:
                with self._lock:
                    self.client.login(username, password)
                    return {"ok": True, "username": username, "data": self.client.dashboard()}
            except SDOError:
                pass
        return {"ok": False, "username": username or ""}

    def login(self, username: str, password: str):
        try:
            with self._lock:
                self.client.login(username, password)
                keyring.set_password(SERVICE, username.strip(), password)
                keyring.set_password(SERVICE, "__last_user__", username.strip())
                data = self.client.dashboard()
            return {"ok": True, "username": username.strip(), "data": data}
        except SDOError as error:
            return {"ok": False, "error": str(error)}
        except Exception:
            return {"ok": False, "error": "Не удалось сохранить вход в диспетчере учётных данных Windows"}

    def refresh(self):
        try:
            with self._lock:
                return {"ok": True, "data": self.client.dashboard()}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def assignments(self, course_url: str):
        try:
            with self._lock:
                return {"ok": True, "items": self.client.assignments(course_url)}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def open_site(self, url: str):
        try:
            url = safe_url(url)
            with self._lock:
                if not self.client.username:
                    raise SDOError("Сначала войдите в СДО")
                self.site_target = url
                if self.site_window is None:
                    self.site_window = webview.create_window(
                        "SDO-Reborn — сайт колледжа", BASE + "/login/index.php",
                        width=1200, height=820, min_size=(850, 600),
                    )
                    self.site_window.events.loaded += self._site_loaded
                else:
                    self.site_window.load_url(url)
                    self.site_window.show()
            return {"ok": True}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def _site_loaded(self):
        """Sign the site window in using the credential stored on this PC, then navigate."""
        try:
            current = self.site_window.evaluate_js("window.location.href")
            if not current or not current.startswith(BASE + "/"):
                return
            if "/login/index.php" in current:
                if self.site_login_pending:
                    return
                username = self.client.username
                password = keyring.get_password(SERVICE, username) if username else None
                if not username or not password:
                    return
                script = """(function(){
                  const u=document.querySelector('input[name="username"]');
                  const p=document.querySelector('input[name="password"]');
                  if(!u||!p||!u.form)return false;
                  u.value=%s; p.value=%s; u.form.submit(); return true;
                })()""" % (json.dumps(username), json.dumps(password))
                self.site_login_pending = bool(self.site_window.evaluate_js(script))
                return
            self.site_login_pending = False
            if current != self.site_target and current.startswith(BASE + "/"):
                self.site_window.load_url(self.site_target)
        except Exception:
            # A site layout or navigation change must never expose credentials in logs.
            self.site_login_pending = False

    def open_browser(self, url: str):
        try:
            webbrowser.open(safe_url(url))
            return {"ok": True}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def logout(self):
        username = keyring.get_password(SERVICE, "__last_user__")
        if username:
            try:
                keyring.delete_password(SERVICE, username)
                keyring.delete_password(SERVICE, "__last_user__")
            except keyring.errors.PasswordDeleteError:
                pass
        with self._lock:
            self.client.close()
            self.client = SDOClient()
        if self.site_window:
            self.site_window.destroy()
            self.site_window = None
        return {"ok": True}

    def settings(self):
        return {"autostart": self._autostart_enabled()}

    def set_autostart(self, enabled: bool):
        if os.name != "nt":
            return {"ok": False, "error": "Автозагрузка доступна только в Windows"}
        import winreg

        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                if enabled:
                    command = f'"{sys.executable}"'
                    if not getattr(sys, "frozen", False):
                        command += ' -m sdo_reborn'
                    winreg.SetValueEx(key, "SDO-Reborn", 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, "SDO-Reborn")
                    except FileNotFoundError:
                        pass
            return {"ok": True, "autostart": enabled}
        except OSError:
            return {"ok": False, "error": "Не удалось изменить автозагрузку"}

    def _autostart_enabled(self):
        if os.name != "nt":
            return False
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, "SDO-Reborn")
                return True
        except OSError:
            return False

    def _closing(self):
        if not self._quitting:
            self.window.hide()
            return False
        return True

    def _quit(self):
        self._quitting = True
        self.client.close()
        if self._tray:
            self._tray.stop()
        self.window.destroy()

    def _start_tray(self):
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("Открыть SDO-Reborn", lambda: self.window.show(), default=True),
                pystray.MenuItem("Выход", self._quit),
            )
            self._tray = pystray.Icon("SDO-Reborn", icon_image(), "SDO-Reborn", menu)
            self._tray.run_detached()
        except Exception:
            self._tray = None

    def _background(self):
        while not self._quitting:
            time.sleep(900)
            if not self.client.username:
                continue
            try:
                with self._lock:
                    page = self.client._get("/my/")
                    items = self.client.notifications(page)
                current = {str(item.get("title", "")) + str(item.get("time", "")) for item in items}
                if self._known_notifications and current - self._known_notifications and self._tray:
                    self._tray.notify("Есть новые уведомления в СДО", "SDO-Reborn")
                self._known_notifications = current
            except Exception:
                pass


def main():
    bridge = Bridge()
    window = webview.create_window(
        "SDO-Reborn", str(resource_path("web/index.html")), js_api=bridge,
        width=1180, height=780, min_size=(850, 600), background_color="#111827",
    )
    bridge.window = window
    window.events.closing += bridge._closing

    def startup():
        bridge._start_tray()
        threading.Thread(target=bridge._background, daemon=True).start()

    webview.start(startup, gui="edgechromium", private_mode=False, storage_path=str(Path(os.environ.get("APPDATA", Path.home())) / "SDO-Reborn"))


if __name__ == "__main__":
    main()
