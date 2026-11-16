"""Strictly scoped browser-session client for the college Moodle site."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = "https://sdo.i-spo.ru"
HOST = "sdo.i-spo.ru"
USER_AGENT = "SDO-Reborn/0.1 (unofficial student desktop client)"


class SDOError(Exception):
    """A deliberately non-sensitive error displayed to the user."""


@dataclass
class Course:
    id: int
    title: str
    url: str


@dataclass
class Event:
    title: str
    when: str
    description: str
    url: str
    kind: str


@dataclass
class Assignment:
    title: str
    url: str


def safe_url(value: str) -> str:
    url = urljoin(BASE + "/", value)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port is not None:
        raise SDOError("Ссылка ведёт за пределы СДО")
    return url


class SDOClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.username: str | None = None
        self._password: str | None = None
        self._last_request = 0.0

    def close(self):
        self._password = None
        self.session.close()

    def _request(self, method: str, path: str, *, data=None, json=None) -> requests.Response:
        url = safe_url(path)
        for _ in range(6):
            elapsed = time.monotonic() - self._last_request
            if elapsed < 0.5:
                time.sleep(0.5 - elapsed)
            try:
                response = self.session.request(
                    method, url, data=data, json=json, timeout=20, allow_redirects=False
                )
            except requests.RequestException:
                raise SDOError("Не удалось связаться с СДО") from None
            self._last_request = time.monotonic()
            if response.is_redirect:
                location = response.headers.get("Location", "")
                url = safe_url(location)
                if response.status_code in (301, 302, 303) and method.upper() == "POST":
                    method, data, json = "GET", None, None
                continue
            if response.status_code >= 400:
                raise SDOError(f"СДО вернула ошибку HTTP {response.status_code}")
            return response
        raise SDOError("Слишком много перенаправлений СДО")

    @staticmethod
    def _logged_in(html: str) -> bool:
        return "logout.php" in html

    def login(self, username: str, password: str):
        username = username.strip()
        if not username or not password:
            raise SDOError("Введите логин и пароль")
        self.session.cookies.clear()
        page = BeautifulSoup(self._request("GET", "/login/index.php").text, "html.parser")
        token = page.select_one('input[name="logintoken"]')
        fields = {"username": username, "password": password}
        if token:
            fields["logintoken"] = token.get("value", "")
        response = self._request("POST", "/login/index.php", data=fields)
        fields.clear()
        if not self._logged_in(response.text):
            raise SDOError("Вход не удался. Проверьте логин и пароль на сайте")
        self.username = username
        self._password = password

    def _get(self, path: str) -> str:
        response = self._request("GET", path)
        if "/login/index.php" in response.url or not self._logged_in(response.text):
            if not self.username or not self._password:
                raise SDOError("Нужно войти в СДО")
            self.login(self.username, self._password)
            response = self._request("GET", path)
            if not self._logged_in(response.text):
                raise SDOError("Сессия СДО истекла")
        return response.text

    def dashboard(self) -> dict:
        my_html = self._get("/my/")
        calendar_html = self._get("/calendar/view.php?view=upcoming")
        return {
            "courses": [asdict(item) for item in parse_courses(my_html)],
            "events": [asdict(item) for item in parse_events(calendar_html)],
            "deadlines": self.deadlines(my_html),
            "notifications": self.notifications(my_html),
        }

    def _ajax(self, page_html: str, method: str, args: dict):
        match = re.search(r'''["']?sesskey["']?\s*[:=]\s*["']?([a-zA-Z0-9]+)''', page_html)
        if not match:
            return None
        payload = [{"index": 0, "methodname": method, "args": args}]
        try:
            response = self._request(
                "POST", f"/lib/ajax/service.php?sesskey={match.group(1)}&info={method}", json=payload
            )
            result = response.json()
            if isinstance(result, list) and result and not result[0].get("error"):
                return result[0].get("data")
        except (SDOError, ValueError, KeyError, TypeError):
            pass
        return None

    def deadlines(self, my_html: str) -> list[dict]:
        data = self._ajax(
            my_html, "core_calendar_get_action_events_by_timesort",
            {"timesortfrom": int(time.time()), "timesortto": int(time.time()) + 90 * 86400, "limitnum": 30},
        )
        if not isinstance(data, dict):
            return []
        events = data.get("events", [])
        result = []
        for event in events if isinstance(events, list) else []:
            try:
                url = safe_url(event.get("url", ""))
            except SDOError:
                continue
            result.append({"title": str(event.get("name", "Задание")), "when": event.get("timesort"), "url": url})
        return result

    def notifications(self, my_html: str) -> list[dict]:
        page = self._get("/message/output/popup/notifications.php")
        soup = BeautifulSoup(page, "html.parser")
        area = soup.select_one('[data-region="notification-area"][data-user-id]')
        if not area:
            return []
        try:
            user_id = int(area["data-user-id"])
        except (ValueError, TypeError):
            return []
        data = self._ajax(my_html, "message_popup_get_popup_notifications", {"useridto": user_id, "limit": 20, "offset": 0})
        if not isinstance(data, dict):
            return []
        result = []
        for item in data.get("notifications", []):
            if not isinstance(item, dict):
                continue
            subject = BeautifulSoup(str(item.get("subject", "Уведомление")), "html.parser").get_text(" ", strip=True)
            result.append({"title": subject, "time": item.get("timecreated")})
        return result

    def assignments(self, course_url: str) -> list[dict]:
        parsed = urlparse(safe_url(course_url))
        if parsed.path != "/course/view.php" or not parse_qs(parsed.query).get("id"):
            raise SDOError("Некорректная ссылка на курс")
        soup = BeautifulSoup(self._get(course_url), "html.parser")
        seen = set()
        result = []
        for a in soup.select('a[href*="/mod/assign/view.php"]'):
            try:
                url = safe_url(a.get("href", ""))
            except SDOError:
                continue
            if url in seen:
                continue
            seen.add(url)
            result.append(asdict(Assignment(a.get_text(" ", strip=True) or "Задание", url)))
        return result


def parse_courses(html: str) -> list[Course]:
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    courses = []
    for a in soup.select('a[href*="/course/view.php?id="]'):
        try:
            url = safe_url(a.get("href", ""))
            course_id = int(parse_qs(urlparse(url).query)["id"][0])
        except (SDOError, KeyError, ValueError, IndexError):
            continue
        title = a.get("title") or a.get_text(" ", strip=True)
        if course_id in seen or not title:
            continue
        seen.add(course_id)
        courses.append(Course(course_id, title, url))
    return courses


def parse_events(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")
    result = []
    for element in soup.select('[data-type="event"][data-event-id]'):
        title = element.select_one("h3.name")
        when_link = element.select_one('a[href*="calendar/view.php?view=day"]')
        description = element.select_one(".description-content")
        if not title or not when_link:
            continue
        try:
            url = safe_url(when_link.get("href", ""))
        except SDOError:
            continue
        result.append(Event(
            title.get_text(" ", strip=True),
            when_link.parent.get_text(" ", strip=True),
            description.get_text(" ", strip=True) if description else "",
            url,
            element.get("data-event-eventtype", ""),
        ))
    return result
