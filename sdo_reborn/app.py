"""Desktop shell for the local student interface."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import keyring
import webview
from PIL import Image

from .client import SDOClient, SDOError
from .diagnostic_log import logger, setup_logging, LOG_FILE
from .sections import StudentSections
from .documents import StudentFiles


SERVICE = "SDO-Reborn"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / name


def icon_image() -> Image.Image:
    return Image.open(resource_path("assets/icon.png")).convert("RGBA")


class Bridge:
    PUBLIC_METHODS = (
        "bootstrap", "resume", "login", "refresh", "topics", "curriculum",
        "programs", "student_section", "course_image", "pdf_page", "log_event",
        "get_log_path", "conversations", "conversation_messages", "send_message",
        "search_people", "send_to_user",
        "recordings", "activity_pick", "activity_submit",
        "logout", "settings", "set_autostart",
    )

    def __dir__(self):
        # Avoid recursive inspection of WebView2's COM objects by pywebview.
        return list(self.PUBLIC_METHODS)

    def __init__(self):
        self.client = SDOClient()
        self.files = StudentFiles(self.client)
        self.student_sections = StudentSections(self.client, self.files)
        self.window = None
        self.cached_data = None
        self._activity_files = {}
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
        except Exception as error:
            logger.error("auth_resume_failed type=%s", type(error).__name__)
            return {"ok": False, "error": "Не удалось прочитать сохранённый вход"}

    def login(self, username: str, password: str):
        logger.info("auth_login_start")
        try:
            with self._lock:
                self.client.login(username, password)
                self.files = StudentFiles(self.client)
                self.student_sections = StudentSections(self.client, self.files)
                self._activity_files.clear()
                keyring.set_password(SERVICE, username.strip(), password)
                keyring.set_password(SERVICE, "__last_user__", username.strip())
                data = self.client.dashboard()
                self.cached_data = data
            logger.info("auth_login_success")
            return {"ok": True, "username": username.strip(), "data": data}
        except SDOError as error:
            logger.warning("auth_login_failed type=SDOError")
            return {"ok": False, "error": str(error)}
        except Exception as error:
            logger.error("auth_login_failed type=%s", type(error).__name__)
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
        return self._section_operation("course", self.student_sections.course, course_url)

    def curriculum(self, program=""):
        try:
            with self._lock:
                programs = self.student_sections.programs()
                if not programs:
                    raise SDOError("СДО не показала учебные программы")
                if not program:
                    program = next((item["id"] for item in programs if item["active"]), programs[0]["id"])
                if not any(item["id"] == program for item in programs):
                    raise SDOError("Учебная программа не найдена в текущей учётной записи")
                items = self.client.curriculum(program)
                logger.info("curriculum_loaded count=%d", len(items))
                return {"ok": True, "items": items}
        except SDOError as error:
            logger.warning("curriculum_failed type=SDOError")
            return {"ok": False, "error": str(error)}

    def programs(self):
        return self._section_operation("programs", self.student_sections.programs)

    def student_section(self, section, program="", material_url=""):
        section_name = section if section in {
            "documents", "schedule", "grades", "official", "webinars", "news",
            "profile", "material", "event", "calendar_day", "recording",
        } else "unknown"
        return self._section_operation(
            section_name, self.student_sections.read, section, program, material_url
        )

    def course_image(self, url):
        return self._section_operation("image", self.files.image, url)

    def pdf_page(self, url, index):
        return self._section_operation("pdf", self.files.pdf_page, url, index)

    def recordings(self, page, title):
        return self._section_operation("recordings", self.student_sections.recordings, page, title)

    def activity_pick(self, token):
        try:
            section = self.student_sections
            limit = section.forms.upload_limit(token)
            if limit is not None and limit <= 0:
                raise SDOError("Все места для вложений заняты")
            result = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=limit != 1)
            if not result:
                return {"ok": True, "names": None}
            if limit is not None and len(result) > limit:
                raise SDOError(f"Можно выбрать не больше {limit} файлов")
            with self._lock:
                if section is not self.student_sections:
                    raise SDOError("Учётная запись изменилась")
                section.forms.upload_limit(token)
                self._activity_files[token] = result
                while len(self._activity_files) > 35:
                    del self._activity_files[next(iter(self._activity_files))]
            return {"ok": True, "names": [Path(entry).name for entry in result]}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def activity_submit(self, token, button, values, include_files):
        with self._lock:
            selected = self._activity_files.pop(token, ())
            return self._section_operation(
                "activity_submit", self.student_sections.submit_activity, token, button, values,
                selected if include_files else (),
            )

    def _section_operation(self, name, operation, *arguments):
        started = time.monotonic()
        logger.info("section_start section=%s", name)
        try:
            with self._lock:
                if not self.client.username:
                    raise SDOError("Сначала войдите в СДО")
                data = operation(*arguments)
            logger.info("section_loaded section=%s ms=%d", name, (time.monotonic() - started) * 1000)
            return {"ok": True, "data": data}
        except SDOError as error:
            logger.warning("section_failed section=%s type=SDOError", name)
            return {"ok": False, "error": str(error)}
        except Exception as error:
            logger.error("section_failed section=%s type=%s", name, type(error).__name__)
            return {"ok": False, "error": "Не удалось прочитать ответ СДО"}

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

    def search_people(self, query):
        return self._section_operation("people_search", self.client.search_people, query)

    def send_to_user(self, user_id, body):
        return self._section_operation("direct_message", self.client.send_to_user, user_id, body)

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

    def logout(self):
        logger.info("auth_logout")
        username = keyring.get_password(SERVICE, "__last_user__")
        if username:
            try:
                keyring.delete_password(SERVICE, username)
                keyring.delete_password(SERVICE, "__last_user__")
            except keyring.errors.PasswordDeleteError:
                logger.info("auth_logout_saved_entry_absent")
        with self._lock:
            self.client.close()
            self.client = SDOClient()
            self.files = StudentFiles(self.client)
            self.student_sections = StudentSections(self.client, self.files)
            self._activity_files.clear()
            self.cached_data = None
        self.window.clear_cookies()
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
                        logger.info("autostart_entry_already_absent")
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
    # Each running copy must serve its own UI assets, including when an older copy is in the tray.
    webview.settings["DEFAULT_HTTP_PORT"] = 0
    bridge = Bridge()
    window = webview.create_window(
        "ОСЭК-Remastered", str(resource_path("web/index.html")), js_api=bridge,
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
