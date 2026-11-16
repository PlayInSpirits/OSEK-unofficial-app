"""Проверка входа и AJAX СДО. Ничего не сохраняет на диск."""

from __future__ import annotations

import getpass
import json
import re
import sys
import time
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


BASE = "https://sdo.i-spo.ru"
USER_AGENT = "CollegeSDODesktop-Diagnostics/0.1 (student client; read-only check)"


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname != "sdo.i-spo.ru":
            raise RuntimeError("Сайт перенаправил запрос за пределы sdo.i-spo.ru")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden: dict[str, str] = {}
        self.logout = False
        self.user_id: int | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("type", "").lower() == "hidden" and a.get("name"):
            self.hidden[a["name"]] = a.get("value", "")
        if tag == "a" and "logout.php" in a.get("href", ""):
            self.logout = True
        if a.get("data-region") == "notification-area":
            value = a.get("data-user-id", "")
            if value.isdecimal():
                self.user_id = int(value)


def parse_page(body: bytes) -> PageParser:
    page = PageParser()
    page.feed(body.decode("utf-8", errors="replace"))
    return page


def read_response(opener, path: str, data: bytes | None = None) -> bytes:
    request = Request(
        BASE + path,
        data=data,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html, application/json"},
    )
    with opener.open(request, timeout=20) as response:
        if urlparse(response.url).hostname != "sdo.i-spo.ru":
            raise RuntimeError("Ответ получен с другого домена")
        return response.read(3_000_000)


def check_ajax(opener, sesskey: str, name: str, arguments: dict) -> str:
    payload = json.dumps([{"index": 0, "methodname": name, "args": arguments}]).encode("utf-8")
    path = "/lib/ajax/service.php?" + urlencode({"sesskey": sesskey, "info": name})
    try:
        body = read_response(opener, path, payload)
        result = json.loads(body)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return f"ошибка транспорта или формата ({type(exc).__name__})"
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        return "неожиданный формат ответа"
    item = result[0]
    if item.get("error"):
        code = str(item.get("exception", {}).get("errorcode", "неизвестно"))
        code = re.sub(r"[^a-zA-Z0-9_ -]", "", code)[:60]
        return f"ошибка Moodle: {code}"
    data = item.get("data")
    if isinstance(data, dict):
        return "работает; поля: " + ", ".join(sorted(data.keys())[:12])
    if isinstance(data, list):
        return f"работает; элементов: {len(data)}"
    return "работает; пустой или простой ответ"


def main() -> int:
    print("Диагностика СДО. Пароль вводится только здесь и не сохраняется.")
    username = input("Логин: ").strip()
    password = getpass.getpass("Пароль (не отображается): ")
    if not username or not password:
        print("Логин и пароль обязательны.")
        return 2
    opener = build_opener(HTTPCookieProcessor(CookieJar()), SafeRedirect())
    try:
        login_page = parse_page(read_response(opener, "/login/index.php"))
        fields = {"username": username, "password": password}
        if "logintoken" in login_page.hidden:
            fields["logintoken"] = login_page.hidden["logintoken"]
        del password
        response = read_response(opener, "/login/index.php", urlencode(fields).encode("utf-8"))
        fields.clear()
        if not parse_page(response).logout:
            print("Вход не подтверждён. Проверьте данные или откройте сайт в браузере.")
            return 1
        print("Вход: успешно")
        dashboard = read_response(opener, "/my/")
        match = re.search(rb'''["']?sesskey["']?\s*[:=]\s*["']?([a-zA-Z0-9]+)''', dashboard)
        if not match:
            print("Ключ сессии на странице не найден; AJAX проверить нельзя.")
            return 1
        sesskey = match.group(1).decode("ascii")
        notification_page = parse_page(read_response(opener, "/message/output/popup/notifications.php"))
        user_id = notification_page.user_id
        if user_id is None:
            print("ID пользователя на странице уведомлений не найден; проверка уведомлений пропущена.")
        checks = [
            ("Курсы", "core_course_get_enrolled_courses_by_timeline_classification", {"classification": "all", "limit": 0, "offset": 0}),
            ("Сроки", "core_calendar_get_action_events_by_timesort", {"timesortfrom": int(time.time()), "timesortto": int(time.time()) + 90 * 86400, "limitnum": 20}),
        ]
        if user_id is not None:
            checks.append(("Уведомления", "message_popup_get_popup_notifications", {"useridto": user_id, "limit": 20, "offset": 0}))
        for label, method, args in checks:
            time.sleep(0.8)
            print(f"{label}: {check_ajax(opener, sesskey, method, args)}")
        print("Проверка окончена. Скопируйте только строки с результатами, без логина и пароля.")
        return 0
    except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
        print(f"Проверка прервана: {type(exc).__name__}. Данные ответа не выводятся.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
