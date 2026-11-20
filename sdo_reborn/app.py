"""Desktop shell for the local student interface."""

from __future__ import annotations

import os
import json
import hashlib
import tempfile
import sys
import threading
import time
from pathlib import Path
from contextlib import contextmanager

import keyring
import webview
from PIL import Image

from .client import SDOClient, SDOError
from .study_ai import StudyAI
from .diagnostic_log import logger, setup_logging, LOG_DIR, LOG_FILE
from .sections import StudentSections
from .documents import StudentFiles
from .document_check import DocumentError, read_document
from .document_web import GROUPS, check_online, open_check_link
from .updates import acknowledge_restart, check_release, stage_update, RELEASE_INTERVAL, UPDATE_FAILURE, UpdateError


SERVICE = "SDO-Reborn"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MUTED_NOTIFICATIONS = LOG_DIR / "notifications-muted"
APPEARANCE_FILE = LOG_DIR / "appearance.txt"
APPEARANCES = {"night", "cobalt", "cream", "pine", "plum"}


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return root / name


def icon_image() -> Image.Image:
    return Image.open(resource_path("assets/icon.png")).convert("RGBA")


class SessionLock:
    """Keep the HTTP session serialized and let foreground work pass queued reads."""

    def __init__(self):
        self.condition = threading.Condition()
        self.owner = None
        self.depth = 0
        self.urgent = 0

    @contextmanager
    def hold(self, priority=False):
        ident = threading.get_ident()
        with self.condition:
            if self.owner != ident:
                self.urgent += int(priority)
                try:
                    self.condition.wait_for(
                        lambda: self.owner is None and (priority or self.urgent == 0)
                    )
                    self.owner = ident
                finally:
                    self.urgent -= int(priority)
            self.depth += 1
        try:
            yield
        finally:
            with self.condition:
                self.depth -= 1
                if not self.depth:
                    self.owner = None
                    self.condition.notify_all()

class Bridge:
    PUBLIC_METHODS = (
        "study_ai", "study_pdf_summary", "study_ai_settings", "save_study_ai_settings",
        "bootstrap", "resume", "login", "refresh", "dashboard_panel", "load_home_cache", "save_home_cache", "topics", "curriculum",
        "programs", "student_section", "course_image", "pdf_page", "log_event",
        "get_log_path", "conversations", "conversation_messages", "send_message",
        "search_people", "send_to_user", "mark_conversation_read", "mark_notification_read",
        "recordings", "activity_pick", "activity_submit", "app_version", "check_for_update",
        "logout", "settings", "set_autostart", "set_notification_sounds", "course_progress",
        "window_command", "refresh_update", "install_update", "update_progress",
        "document_pick", "document_check", "document_check_progress", "document_open_link", "set_appearance",
        "set_meeting_subscription",
    )

    def __dir__(self):
        # Avoid recursive inspection of WebView2's COM objects by pywebview.
        return list(self.PUBLIC_METHODS)

    def __init__(self):
        self.client = SDOClient()
        self._study_ai = StudyAI(LOG_DIR / "study-ai.sqlite3")
        self.files = StudentFiles(self.client)
        self.student_sections = StudentSections(self.client, self.files)
        self.window = None
        self.cached_data = None
        self._activity_files = {}
        self._lock = SessionLock()
        self._home_cache_lock = threading.Lock()
        self._tray = None
        self._quitting = False
        self._maximized = False
        self._stop = threading.Event()
        self._installed_version = int(resource_path("web/version.txt").read_text(encoding="ascii").strip())
        self._release_status = {"ok": False}
        self._release_check_lock = threading.Lock()
        self._install_lock = threading.Lock()
        self._update_progress = {"phase": "idle"}
        self._documents = {}
        self._document_check_lock = threading.Lock()
        self._document_progress = {"completed": 0, "total": 0}

    def bootstrap(self):
        try:
            username = keyring.get_password(SERVICE, "__last_user__")
            password = keyring.get_password(SERVICE, username) if username else None
        except Exception:
            return {"ok": False, "username": ""}
        return {"saved": bool(username and password), "username": username or ""}

    @staticmethod
    def _home_cache_path(username):
        key = hashlib.sha256(username.encode("utf-8")).hexdigest()
        return LOG_DIR / "dashboard-cache" / (key + ".json")

    def load_home_cache(self):
        with self._home_cache_lock:
            username = self.client.username
            if not username:
                return {}
            try:
                path = self._home_cache_path(username)
                if path.stat().st_size <= 2_000_000:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        panels = data.get("panels")
                        if isinstance(panels, dict):
                            panels.pop("deadlines", None)
                        return data
            except (OSError, ValueError):
                pass
            return {}

    def save_home_cache(self, username, data):
        with self._home_cache_lock:
            if not self.client.username or username != self.client.username or not isinstance(data, dict):
                return
            keys = ("programs", "plans", "progress", "days", "panels")
            snapshot = {key: data[key] for key in keys if key in data}
            panels = snapshot.get("panels")
            if isinstance(panels, dict):
                snapshot["panels"] = {key: panels[key] for key in ("calendar",) if key in panels}
            text = json.dumps(snapshot, ensure_ascii=False)
            if len(text.encode("utf-8")) > 2_000_000:
                return
            path = self._home_cache_path(username)
            temp = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=path.parent, encoding="utf-8", mode="w", delete=False) as handle:
                    temp = Path(handle.name)
                    handle.write(text)
                temp.replace(path)
            except OSError:
                logger.warning("home_cache_write_failed")
            finally:
                if temp:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass

    def app_version(self):
        return f"v0.{self._installed_version}"

    def window_command(self, command):
        if command == "minimize":
            self.window.minimize()
        elif command == "toggle_maximize":
            if self._maximized:
                self.window.restore()
            else:
                self.window.maximize()
        elif command == "close":
            self.window.destroy()

    def _window_maximized(self):
        self._maximized = True
        self.window.evaluate_js("window.setWindowMaximized(true)")

    def _window_restored(self):
        self._maximized = False
        self.window.evaluate_js("window.setWindowMaximized(false)")

    def check_for_update(self):
        result = self._release_status
        try:
            failure = UPDATE_FAILURE.read_text(encoding="utf-8")
        except OSError:
            failure = ""
        return {
            "ok": result["ok"], "version": result.get("version"), "name": result.get("name"),
            "url": result.get("url"), "automatic": bool(getattr(sys, "frozen", False) and result.get("sha256")),
            "failure": failure,
        }

    def refresh_update(self):
        with self._release_check_lock:
            result = check_release(self._installed_version)
            if result["ok"]:
                self._release_status = result
        return {"ok": result["ok"]}

    def update_progress(self):
        return self._update_progress

    def install_update(self):
        release = self._release_status
        if not self.client.username or not getattr(sys, "frozen", False) or not release.get("sha256") or not release.get("version"):
            return {"ok": False, "error": "Автоматическая установка этой версии недоступна."}
        if not self._install_lock.acquire(blocking=False):
            return {"ok": False, "error": "Обновление уже выполняется."}
        self._update_progress = {"phase": "download", "received": 0, "total": release["size"]}

        def on_progress(received, total):
            self._update_progress = {"phase": "download", "received": received, "total": total}

        def install():
            try:
                stage_update(release, Path(sys.executable), resource_path("sdo_reborn/install_update.ps1"), on_progress)
            except Exception as error:
                logger.warning("update_install_failed type=%s", type(error).__name__)
                message = str(error) if isinstance(error, UpdateError) else "Не удалось скачать или распаковать обновление. Проверьте подключение и права записи в папку приложения."
                self._update_progress = {"phase": "error", "error": message}
                self._install_lock.release()
                return
            self._update_progress = {"phase": "restart"}
            time.sleep(0.65)
            self._quit()

        threading.Thread(target=install, daemon=True).start()
        return {"ok": True}

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
            with self._lock.hold():
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
            with self._lock.hold():
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
            with self._lock.hold():
                self.cached_data = self.client.dashboard()
                logger.info("data_refresh_success")
                return {"ok": True, "data": self.cached_data}
        except SDOError as error:
            logger.warning("data_refresh_failed type=SDOError")
            return {"ok": False, "error": str(error)}

    def dashboard_panel(self, panel):
        with self._lock.hold():
            return self._section_operation("dashboard_panel", self.client.dashboard_panel, panel)

    def assignments(self, course_url: str):
        try:
            with self._lock.hold():
                return {"ok": True, "items": self.client.assignments(course_url)}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def topics(self, course_url: str):
        return self._section_operation("course", self.student_sections.course, course_url, priority=True)

    def course_progress(self, course_url: str):
        return self._section_operation("course_progress", self.student_sections.course_progress, course_url)

    def curriculum(self, program=""):
        try:
            with self._lock.hold():
                programs = self.student_sections.programs()
                if not programs:
                    raise SDOError("СДО не показала учебные программы")
                if not program:
                    program = next((item["id"] for item in programs if item["active"]), programs[0]["id"])
                if not any(item["id"] == program for item in programs):
                    raise SDOError("Учебная программа не найдена в текущей учётной записи")
                items = self.student_sections.curriculum(program)
                logger.info("curriculum_loaded count=%d", len(items))
                return {"ok": True, "items": items}
        except SDOError as error:
            logger.warning("curriculum_failed type=SDOError")
            return {"ok": False, "error": str(error)}

    def programs(self):
        return self._section_operation("programs", self.student_sections.programs)

    def student_section(self, section, program="", material_url="", foreground=False):
        section_name = section if section in {
            "documents", "schedule", "grades", "official", "webinars", "news",
            "profile", "material", "event", "calendar_day", "recording", "teacher",
        } else "unknown"
        return self._section_operation(
            section_name, self.student_sections.read, section, program, material_url,
            priority=foreground is True and section == "material",
        )

    def course_image(self, url):
        return self._section_operation("image", self.files.image, url)

    def set_meeting_subscription(self, identity, subscribed):
        return self._section_operation("meeting_subscription", self.student_sections.set_meeting_subscription, identity, subscribed)

    def _study_config(self):
        if not self.client.username:
            return {}
        try:
            config = json.loads(keyring.get_password(SERVICE + "-study-ai", self.client.username) or "{}")
            if not isinstance(config, dict):
                return {}
            if config.get("mode") == "groq":
                return {"mode": "gemini"}
            return config
        except Exception:
            return {}

    def study_ai_settings(self):
        config = self._study_config()
        return {"mode": config.get("mode", "relay" if os.environ.get("OSEK_AI_URL") else "gemini"), "url": config.get("url", ""), "hasKey": bool(config.get("key"))}

    def save_study_ai_settings(self, mode, url, key, clear=False):
        from urllib.parse import urlsplit
        if not self.client.username or mode not in ("gemini", "relay") or not isinstance(url, str) or not isinstance(key, str):
            return {"ok": False, "error": "Некорректные настройки ИИ."}
        url, key = url.strip(), key.strip()
        try:
            if mode == "relay" and url:
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or len(url) > 500:
                    raise ValueError()
            if len(key) > 1024 or any(not 33 <= ord(c) <= 126 for c in key):
                raise ValueError()
            previous = self._study_config()
            if not key and not clear and previous.get("mode") == mode:
                key = previous.get("key", "")
            keyring.set_password(SERVICE + "-study-ai", self.client.username,
                                 json.dumps({"mode": mode, "url": url if mode == "relay" else "", "key": key}))
            return {"ok": True}
        except ValueError:
            return {"ok": False, "error": "Проверьте HTTPS-адрес и формат ключа."}
        except Exception:
            return {"ok": False, "error": "Хранилище учётных данных Windows недоступно."}

    def study_ai(self, scope, payload):
        return self._study_ai.run(self.client.username, "question", scope, payload, self._study_config())

    def study_pdf_summary(self, scope, url):
        config = self._study_config()
        if config.get("mode") == "gemini":
            field, load = "pdf", self.files.pdf_document
        else:
            field, load = "text", self.files.pdf_text
        answer = self._section_operation(field, load, url)
        if not answer["ok"]:
            return answer
        return self._study_ai.run(self.client.username, "summary", scope, {field: answer["data"]}, config)

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
            with self._lock.hold():
                if section is not self.student_sections:
                    raise SDOError("Учётная запись изменилась")
                section.forms.upload_limit(token)
                self._activity_files[token] = result
                while len(self._activity_files) > 35:
                    del self._activity_files[next(iter(self._activity_files))]
            return {"ok": True, "names": [Path(entry).name for entry in result]}
        except SDOError as error:
            return {"ok": False, "error": str(error)}

    def document_pick(self):
        section = self.student_sections
        result = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False)
        if not result:
            return {"ok": True, "cancelled": True}
        try:
            entry = read_document(result[0])
        except (DocumentError, OSError, UnicodeError, ValueError) as error:
            message = str(error) if isinstance(error, DocumentError) else "Не удалось прочитать выбранный файл."
            return {"ok": False, "error": message}
        with self._lock.hold():
            if section is not self.student_sections:
                return {"ok": False, "error": "Учётная запись изменилась."}
            self._documents["primary"] = entry
        return {"ok": True, "file": entry.metadata()}

    def document_check(self, group):
        if not isinstance(group, str) or group not in GROUPS:
            return {"ok": False, "error": "Выберите тип работы."}
        if not self._document_check_lock.acquire(blocking=False):
            return {"ok": False, "error": "Проверка уже выполняется."}
        self._document_progress = {"completed": 0, "total": 0}
        try:
            with self._lock.hold():
                document = self._documents.get("primary")
                section = self.student_sections
            if document is None:
                return {"ok": False, "error": "Сначала выберите документ."}

            def report(completed, total):
                self._document_progress = {"completed": completed, "total": total}

            cancelled = lambda: section is not self.student_sections or self._stop.is_set()
            results = check_online(document, group, report, cancelled)
            return {"ok": True, "results": results}
        except DocumentError as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:
            logger.warning("document_check_failed type=%s", type(error).__name__)
            return {"ok": False, "error": "Не удалось завершить поиск совпадений."}
        finally:
            self._document_check_lock.release()

    def document_check_progress(self):
        return self._document_progress

    def document_open_link(self, url):
        return {"ok": open_check_link(url)}

    def activity_submit(self, token, button, values, include_files):
        with self._lock.hold(priority=True):
            selected = self._activity_files.pop(token, ())
            return self._section_operation(
                "activity_submit", self.student_sections.submit_activity, token, button, values,
                selected if include_files else (),
            )

    def _section_operation(self, name, operation, *arguments, priority=False):
        started = time.monotonic()
        logger.info("section_start section=%s", name)
        try:
            with self._lock.hold(priority):
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
            with self._lock.hold():
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

    def mark_conversation_read(self, conversation_id):
        return self._section_operation("conversation_read", self.client.mark_conversation_read, conversation_id)

    def mark_notification_read(self, notification_id):
        return self._section_operation("notification_read", self.client.mark_notification_read, notification_id)

    def conversation_messages(self, conversation_id: int):
        try:
            with self._lock.hold():
                rows = self.client.conversation_messages(conversation_id)
            logger.info("messages_thread_loaded count=%d", len(rows))
            return {"ok": True, "items": rows}
        except Exception as error:
            logger.warning("messages_thread_failed type=%s", type(error).__name__)
            return {"ok": False, "error": str(error) if isinstance(error, SDOError) else "Не удалось загрузить переписку"}

    def send_message(self, conversation_id: int, text: str):
        try:
            with self._lock.hold():
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
        with self._lock.hold(priority=True), self._home_cache_lock:
            cache_owner = self.client.username or username
            self.client.close()
            self.client = SDOClient()
            if cache_owner:
                try:
                    self._home_cache_path(cache_owner).unlink(missing_ok=True)
                except OSError:
                    logger.warning("home_cache_delete_failed")
            self.files = StudentFiles(self.client)
            self.student_sections = StudentSections(self.client, self.files)
            self._activity_files.clear()
            self.cached_data = None
            self._documents.clear()
        self.window.clear_cookies()
        return {"ok": True}

    def settings(self):
        try:
            theme = APPEARANCE_FILE.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            theme = "night"
        return {"autostart": self._autostart_enabled(), "sounds": not MUTED_NOTIFICATIONS.exists(),
                "theme": theme if theme in APPEARANCES else "night"}

    def set_appearance(self, theme):
        if not isinstance(theme, str) or theme not in APPEARANCES:
            return {"ok": False}
        try:
            with self._lock.hold():
                temp = APPEARANCE_FILE.with_suffix(".tmp")
                temp.write_text(theme, encoding="ascii")
                temp.replace(APPEARANCE_FILE)
            return {"ok": True}
        except OSError:
            return {"ok": False}

    def set_notification_sounds(self, enabled: bool):
        try:
            if enabled:
                MUTED_NOTIFICATIONS.unlink(missing_ok=True)
            else:
                MUTED_NOTIFICATIONS.touch()
            return {"ok": True}
        except OSError:
            logger.warning("notification_setting_failed")
            return {"ok": False, "error": "Не удалось сохранить настройки звука"}

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
        if not self._quitting and self._install_lock.locked():
            return False
        if not self._quitting and self._tray and self._tray.visible:
            self.window.hide()
            return False
        return True

    def _quit(self):
        self._quitting = True
        self._stop.set()
        try:
            self.client.close()
        finally:
            try:
                if self._tray:
                    self._tray.stop()
            finally:
                self.window.destroy()

    def _start_tray(self):
        try:
            import pystray
            from pystray._util import win32

            class NotificationIcon(pystray.Icon):
                def _notify(self, message, title=None):
                    # Windows' NIIF_NOSOUND suppresses the balloon's default sound.
                    self._message(
                        win32.NIM_MODIFY, win32.NIF_INFO, szInfo=message,
                        szInfoTitle=title or self.title, dwInfoFlags=0x10,
                    )

            menu = pystray.Menu(
                pystray.MenuItem("Открыть ОСЭК-Remastered", lambda: self.window.show(), default=True),
                pystray.MenuItem("Выход", self._quit),
            )
            self._tray = NotificationIcon("ОСЭК-Remastered", icon_image(), "ОСЭК-Remastered", menu)
            ready = threading.Event()

            def show_icon(icon):
                icon.visible = True
                ready.set()

            self._tray.run_detached(setup=show_icon)
            ready.wait(timeout=2)
        except Exception:
            self._tray = None

    def _notify(self, message, sound):
        if self._quitting:
            return False
        delivered = bool(self._tray and self._tray.visible)
        if delivered:
            self._tray.notify(message, "ОСЭК-Remastered")
        if os.name == "nt" and not MUTED_NOTIFICATIONS.exists():
            import winsound

            try:
                winsound.PlaySound(sound, winsound.SND_ALIAS | winsound.SND_ASYNC)
                delivered = True
            except RuntimeError:
                logger.warning("notification_sound_failed")
        return delivered

    def _release_background(self):
        while not self._stop.is_set():
            try:
                self.refresh_update()
            except Exception as error:
                logger.warning("release_poll_failed type=%s", type(error).__name__)
            if self._stop.wait(RELEASE_INTERVAL):
                break

    def _background(self):
        previous_notifications = None
        previous_unread = None
        previous_account = None
        while not self._stop.wait(120):
            try:
                with self._lock.hold():
                    if not self.client.username:
                        previous_account = None
                        continue
                    account = (self.client, self.client.username)
                    if account != previous_account:
                        previous_notifications = previous_unread = None
                        previous_account = account
                    items = self.client.notifications(self.client._get("/my/"))
                current = {(item.get("title", ""), item.get("time", "")) for item in items}
                if previous_notifications is not None and current - previous_notifications:
                    self._notify("Есть новые уведомления в СДО", "SystemNotification")
                previous_notifications = current
                with self._lock.hold():
                    if account != (self.client, self.client.username):
                        continue
                    conversations = self.client.conversations()
                current_unread = {row["id"]: int(row.get("unread") or 0) for row in conversations}
                if previous_unread is not None and any(
                    count > previous_unread.get(key, 0) for key, count in current_unread.items()
                ):
                    self._notify("Есть новые сообщения в СДО", "SystemNotification")
                previous_unread = current_unread
            except Exception as error:
                logger.warning("notification_poll_failed type=%s", type(error).__name__)


def main():
    setup_logging()
    # Each running copy must serve its own UI assets, including when an older copy is in the tray.
    webview.settings["DEFAULT_HTTP_PORT"] = 0
    bridge = Bridge()
    window = webview.create_window(
        "ОСЭК-Remastered", str(resource_path("web/index.html")), js_api=bridge,
        width=1180, height=780, min_size=(850, 600), background_color="#091632",
        frameless=True, easy_drag=False,
    )
    bridge.window = window
    window.events.closing += bridge._closing
    window.events.maximized += bridge._window_maximized
    window.events.restored += bridge._window_restored
    window.events.loaded += lambda: acknowledge_restart(Path(sys.executable))

    def startup():
        bridge._start_tray()
        threading.Thread(target=bridge._release_background, daemon=True).start()
        threading.Thread(target=bridge._background, daemon=True).start()

    webview.start(startup, gui="edgechromium", private_mode=False, storage_path=str(Path(os.environ.get("APPDATA", Path.home())) / "SDO-Reborn"))


if __name__ == "__main__":
    main()
