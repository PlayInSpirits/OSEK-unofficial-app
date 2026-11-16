"""Desktop shell. The Moodle assignment form remains the original site inside the app."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import keyring
import webview
from PIL import Image

from .client import BASE, SDOClient, SDOError, safe_url
from .diagnostic_log import logger, setup_logging, LOG_FILE


SERVICE = "SDO-Reborn"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / name


def icon_image() -> Image.Image:
    return Image.open(resource_path("assets/icon.png")).convert("RGBA")


class Bridge:
    def __init__(self):
        self.client = SDOClient()
        self.window = None
        self.site_window = None
        self.site_target = BASE + "/my/"
        self.site_login_pending = False
        self.cached_data = None
        self._lock = threading.RLock()
        self._tray = None
        self._quitting = False
        self._known_notifications: set[str] = set()

    def bootstrap(self):
        try:
            username = keyring.get_password(SERVICE, "__last_user__")
            password = keyring.get_password(SERVICE, username) if username else None
        except Exception:
            return {"ok": False, "username": ""}
        return {"saved": bool(username and password), "username": username or ""}

    def resume(self):
        logger.info("auth_resume_start")
        try:
            if self.client.username and self.cached_data is not None:
                logger.info("auth_resume_cached")
                return {"ok": True, "username": self.client.username, "data": self.cached_data}
            username = keyring.get_password(SERVICE, "__last_user__")
            password = keyring.get_password(SERVICE, username) if username else None
            if not username or not password:
                return {"ok": False, "error": "Сохранённый вход не найден"}
            with self._lock:
                self.client.login(username, password)
                data = self.client.dashboard()
                self.cached_data = data
            logger.info("auth_resume_success")
            return {"ok": True, "username": username, "data": data}
        except SDOError as error:
            logger.warning("auth_resume_failed type=SDOError")
            return {"ok": False, "error": str(error)}
        except Exception:
            logger.exception("auth_resume_failed")
            return {"ok": False, "error": "Не удалось прочитать сохранённый вход"}

    def login(self, username: str, password: str):
        logger.info("auth_login_start")
        try:
            with self._lock:
                self.client.login(username, password)
                keyring.set_password(SERVICE, username.strip(), password)
                keyring.set_password(SERVICE, "__last_user__", username.strip())
                data = self.client.dashboard()
                self.cached_data = data
            logger.info("auth_login_success")
            return {"ok": True, "username": username.strip(), "data": data}
        except SDOError as error:
            logger.warning("auth_login_failed type=SDOError")
            return {"ok": False, "error": str(error)}
        except Exception:
            logger.exception("auth_login_failed")
            return {"ok": False, "error": "Не удалось сохранить вход в диспетчере учётных данных Windows"}

    def refresh(self):
        logger.info("data_refresh_start")
        try:
            with self._lock:
                self.cached_data = self.client.dashboard()
                logger.info("data_refresh_success")
                return {"ok": True, "data": self.cached_data}
        except SDOError as error:
            logger.warning("data_refresh_failed type=SDOError")
            return {"ok": False, "error": str(error)}

    def assignments(self, course_url: str):
        try:
            with self._lock:
                return {"ok": True, "items": self.client.assignments(course_url)}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def topics(self, course_url: str):
        try:
            with self._lock:
                return {"ok": True, "items": self.client.topics(course_url)}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def curriculum(self):
        try:
            with self._lock:
                return {"ok": True, "items": self.client.curriculum()}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def open_site(self, url: str):
        try:
            url = safe_url(url)
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            if parsed.path == "/" and query.get("action", [""])[0] in {"kug", "docs", "notice_official"} and "pid" not in query:
                courses = (self.cached_data or {}).get("courses", [])
                for course in courses:
                    candidate = parse_qs(urlparse(course.get("url", "")).query).get("pid", [""])[0]
                    if candidate.isdecimal():
                        query["pid"] = [candidate]
                        url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
                        break
                if "pid" not in query:
                    raise SDOError("Не найдена учебная программа для этого раздела")
            if not self.client.username:
                raise SDOError("Сначала войдите в СДО")
            self.site_target = url
            self.site_login_pending = False
            logger.info("content_open path=%s", urlparse(url).path)
            self.window.load_url(url)
            return {"ok": True}
        except SDOError as error:
            logger.warning("content_open_failed")
            return {"ok": False, "error": str(error)}

    def back_to_app(self):
        logger.info("content_back_to_app")
        self.window.load_url(str(resource_path("web/index.html")))
        return {"ok": True}

    def log_event(self, kind: str, detail: str = ""):
        if kind in {"tab", "course", "notification", "message_open", "message_send", "ui_error", "button"}:
            logger.info("ui_%s detail=%s", kind, str(detail).replace("\n", " ")[:80])
        return {"ok": True}

    def get_log_path(self):
        return str(LOG_FILE)

    def conversations(self):
        try:
            with self._lock:
                rows = self.client.conversations()
            logger.info("messages_list_loaded count=%d", len(rows))
            return {"ok": True, "items": rows}
        except Exception as error:
            logger.warning("messages_list_failed type=%s", type(error).__name__)
            return {"ok": False, "error": str(error) if isinstance(error, SDOError) else "Не удалось загрузить сообщения"}

    def conversation_messages(self, conversation_id: int):
        try:
            with self._lock:
                rows = self.client.conversation_messages(conversation_id)
            logger.info("messages_thread_loaded count=%d", len(rows))
            return {"ok": True, "items": rows}
        except Exception as error:
            logger.warning("messages_thread_failed type=%s", type(error).__name__)
            return {"ok": False, "error": str(error) if isinstance(error, SDOError) else "Не удалось загрузить переписку"}

    def send_message(self, conversation_id: int, text: str):
        try:
            with self._lock:
                self.client.send_message(conversation_id, text)
            logger.info("message_sent conversation=%d", int(conversation_id))
            return {"ok": True}
        except Exception as error:
            logger.warning("message_send_failed type=%s", type(error).__name__)
            return {"ok": False, "error": str(error) if isinstance(error, SDOError) else "Не удалось отправить сообщение"}

    def _site_loaded(self):
        """Sign the site window in using the credential stored on this PC, then navigate."""
        try:
            current = self.window.evaluate_js("window.location.href")
            if not current or not current.startswith(BASE + "/"):
                return
            logger.info("content_loaded path=%s", urlparse(current).path)
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
                self.site_login_pending = bool(self.window.evaluate_js(script))
                return
            self.site_login_pending = False
            if self.site_target and self.site_target != current and urlparse(current).path in ("/", "/my/"):
                target, self.site_target = self.site_target, ""
                self.window.load_url(target)
                return
            self.site_target = ""
            self.window.evaluate_js("""(function(){if(document.getElementById('osek-return'))return;const b=document.createElement('button');b.id='osek-return';b.textContent='← Вернуться в ОСЭК-Remastered';b.style.cssText='position:fixed;bottom:18px;right:18px;z-index:2147483647;background:#696cf5;color:white;border:0;border-radius:12px;padding:13px 18px;font:600 14px Segoe UI;box-shadow:0 4px 18px #0005;cursor:pointer';b.onclick=()=>window.pywebview.api.back_to_app();document.body.appendChild(b)})()""")
        except Exception:
            logger.exception("content_load_error")
            self.site_login_pending = False

    def logout(self):
        logger.info("auth_logout")
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
            self.cached_data = None
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
                    winreg.SetValueEx(key, "ОСЭК-Remastered", 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, "ОСЭК-Remastered")
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
                winreg.QueryValueEx(key, "ОСЭК-Remastered")
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
                pystray.MenuItem("Открыть ОСЭК-Remastered", lambda: self.window.show(), default=True),
                pystray.MenuItem("Выход", self._quit),
            )
            self._tray = pystray.Icon("ОСЭК-Remastered", icon_image(), "ОСЭК-Remastered", menu)
            self._tray.run_detached()
        except Exception:
            self._tray = None

    def _background(self):
        initialized = False
        while not self._quitting:
            time.sleep(900)
            if not self.client.username:
                continue
            try:
                with self._lock:
                    page = self.client._get("/my/")
                    items = self.client.notifications(page)
                current = {str(item.get("title", "")) + str(item.get("time", "")) for item in items}
                if initialized and current - self._known_notifications and self._tray:
                    self._tray.notify("Есть новые уведомления в СДО", "SDO-Reborn")
                    logger.info("notification_new count=%d", len(current - self._known_notifications))
                self._known_notifications = current
                initialized = True
            except Exception:
                logger.warning("notification_poll_failed")


def main():
    setup_logging()
    bridge = Bridge()
    window = webview.create_window(
        "ОСЭК-Remastered", str(resource_path("web/index.html")), js_api=bridge,
        width=1180, height=780, min_size=(850, 600), background_color="#111827",
    )
    bridge.window = window
    window.events.closing += bridge._closing
    window.events.loaded += bridge._site_loaded

    def startup():
        bridge._start_tray()
        threading.Thread(target=bridge._background, daemon=True).start()

    webview.start(startup, gui="edgechromium", private_mode=False, storage_path=str(Path(os.environ.get("APPDATA", Path.home())) / "SDO-Reborn"))


if __name__ == "__main__":
    main()
