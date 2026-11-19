"""Strictly scoped browser-session client for the college Moodle site."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from .diagnostic_log import logger


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
    id: str = ""


@dataclass
class Assignment:
    title: str
    url: str


def safe_url(value: str) -> str:
    url = urljoin(BASE + "/", value)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port is not None or parsed.username is not None:
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

    def _request(self, method: str, path: str, *, data=None, json=None, stream=False, headers=None) -> requests.Response:
        url = safe_url(path)
        for _ in range(6):
            elapsed = time.monotonic() - self._last_request
            if elapsed < 0.5:
                time.sleep(0.5 - elapsed)
            try:
                started = time.monotonic()
                response = self.session.request(
                    method, url, data=data, json=json, timeout=20, allow_redirects=False,
                    stream=stream, headers=headers,
                )
            except requests.RequestException as error:
                logger.warning("sdo_request_failed method=%s type=%s", method, type(error).__name__)
                raise SDOError("Не удалось связаться с СДО") from None
            logger.info(
                "sdo_response method=%s status=%d ms=%d",
                method, response.status_code, (time.monotonic() - started) * 1000,
            )
            self._last_request = time.monotonic()
            if response.is_redirect:
                location = response.headers.get("Location", "")
                response.close()
                url = safe_url(urljoin(url, location))
                if response.status_code in (301, 302, 303) and method.upper() == "POST":
                    method, data, json = "GET", None, None
                    headers = None
                elif method.upper() == "POST":
                    raise SDOError("СДО перенаправила отправку формы без подтверждения")
                continue
            if response.status_code >= 400:
                response.close()
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
        return self._authenticated_response(path).text

    def _authenticated_response(self, path: str) -> requests.Response:
        response = self._request("GET", path)
        if "/login/index.php" in response.url or not self._logged_in(response.text):
            if not self.username or not self._password:
                raise SDOError("Нужно войти в СДО")
            self.login(self.username, self._password)
            response = self._request("GET", path)
            if not self._logged_in(response.text):
                raise SDOError("Сессия СДО истекла")
        return response

    def dashboard(self) -> dict:
        my_html = self._get("/my/")
        calendar_html = self._get("/calendar/view.php?view=upcoming")
        try:
            notifications = self.notification_snapshot(my_html)
        except SDOError:
            notifications = {"items": [], "unread": 0}
        return {
            "courses": [asdict(item) for item in parse_courses(my_html)],
            "events": [asdict(item) for item in parse_events(calendar_html)],
            "deadlines": self.deadlines(my_html),
            "notifications": notifications["items"],
            "unread_notifications": notifications["unread"],
            "profile": self.profile(my_html),
        }

    @staticmethod
    def profile(html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        name = soup.select_one(".usermenu .usertext")
        avatar = soup.select_one(".usermenu img.userpicture")
        return {
            "name": name.get_text(" ", strip=True) if name else "",
            "avatar": safe_url(avatar.get("src", "")) if avatar and avatar.get("src") else "",
        }

    def _message_context(self):
        html = self._get("/message/index.php")
        soup = BeautifulSoup(html, "html.parser")
        link = soup.select_one('.usermenu a[href*="profile.php"]')
        match = re.search(r"[?&]id=(\d+)", link.get("href", "")) if link else None
        if not match:
            raise SDOError("Не удалось определить учётную запись для сообщений")
        return html, int(match.group(1))

    def conversations(self) -> list[dict]:
        html, user_id = self._message_context()
        data = self._ajax(html, "core_message_get_conversations", {"userid": user_id, "limitfrom": 0, "limitnum": 50})
        own = self._ajax(html, "core_message_get_self_conversation", {"userid": user_id})
        if not isinstance(data, dict):
            raise SDOError("Не удалось загрузить сообщения")
        rows = data.get("conversations", [])
        if isinstance(own, dict) and own.get("id") and not any(row.get("id") == own["id"] for row in rows):
            rows.insert(0, own)
        return [{"id": row.get("id"), "name": row.get("name") or "Сообщения себе", "unread": row.get("unreadcount", 0)} for row in rows if isinstance(row, dict) and row.get("id")]

    def conversation_messages(self, conversation_id: int) -> list[dict]:
        html, user_id = self._message_context()
        data = self._ajax(html, "core_message_get_conversation_messages", {"currentuserid": user_id, "convid": int(conversation_id), "limitfrom": 0, "limitnum": 100})
        if not isinstance(data, dict):
            raise SDOError("Не удалось загрузить переписку")
        result = []
        for row in data.get("messages", []):
            if isinstance(row, dict):
                result.append({"text": BeautifulSoup(str(row.get("text", "")), "html.parser").get_text(" ", strip=True), "mine": row.get("useridfrom") == user_id, "time": row.get("timecreated", 0)})
        return result

    def mark_conversation_read(self, conversation_id: int):
        html, user_id = self._message_context()
        result = self._ajax(html, "core_message_mark_all_conversation_messages_as_read", {
            "userid": user_id, "conversationid": int(conversation_id),
        }, allow_empty=True)
        if result is None:
            raise SDOError("СДО не подтвердила отметку о прочтении переписки")

    def search_people(self, term: str) -> list[dict]:
        if not isinstance(term, str) or not 2 <= len(term.strip()) <= 120:
            raise SDOError("Введите от 2 до 120 символов для поиска")
        html, user_id = self._message_context()
        data = self._ajax(html, "core_message_message_search_users", {
            "userid": user_id, "search": term.strip(), "limitfrom": 0, "limitnum": 20,
        })
        if not isinstance(data, dict):
            raise SDOError("СДО не ответила на поиск собеседников")
        rows = []
        for group in ("contacts", "noncontacts"):
            for person in data.get(group, []):
                conversations = person.get("conversations") or []
                direct = next(
                    (item["id"] for item in conversations if item.get("type") == 1 and item.get("id")),
                    None,
                )
                rows.append({
                    "userId": person["id"], "id": direct, "name": person["fullname"],
                    "canMessage": bool(person.get("canmessage")), "isContact": group == "contacts",
                })
        return rows

    def send_to_user(self, user_id: int, text: str):
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise SDOError("Введите сообщение длиной до 10 000 символов")
        html, _ = self._message_context()
        data = self._ajax(html, "core_message_send_instant_messages", {
            "messages": [{"touserid": int(user_id), "text": text.strip(), "textformat": 0}],
        })
        if not isinstance(data, list) or not data or int(data[0].get("msgid") or 0) <= 0:
            raise SDOError("СДО не подтвердила доставку первого сообщения")

    def send_message(self, conversation_id: int, text: str):
        if not isinstance(text, str) or not text.strip() or len(text) > 10000:
            raise SDOError("Введите сообщение длиной до 10 000 символов")
        html, _ = self._message_context()
        data = self._ajax(html, "core_message_send_messages_to_conversation", {"conversationid": int(conversation_id), "messages": [{"text": text.strip(), "textformat": 0}]})
        if not isinstance(data, list) or not data:
            raise SDOError("СДО не подтвердила отправку сообщения")

    def curriculum(self, pid: str) -> list[dict]:
        if not pid.isdecimal():
            raise SDOError("Некорректная учебная программа")
        soup = BeautifulSoup(self._get(f"/?pid={pid}"), "html.parser")
        result = []
        for card in soup.select(".columnGrid"):
            link = card.select_one('.columnGrid h5 a[href*="/course/view.php"]')
            if not link:
                continue
            teacher = card.select_one(".course-teacher-plan-name")
            teacher_link = teacher.select_one('a[href*="/user/profile.php"]') if teacher else None
            avatar = card.select_one('img.userpicture[src]')
            hours = card.select_one(".time-span")
            control = card.select_one(".control-div")
            grade = card.select_one(".rate-span")
            try:
                url = safe_url(link.get("href", ""))
            except SDOError:
                continue
            teacher_url = ""
            teacher_avatar = ""
            try:
                if teacher_link:
                    teacher_url = safe_url(teacher_link["href"])
                if avatar:
                    teacher_avatar = safe_url(avatar["src"])
            except SDOError:
                logger.info("curriculum_teacher_link_unavailable")
            result.append({
                "title": link.get_text(" ", strip=True),
                "teacher": teacher.get_text(" ", strip=True) if teacher else "",
                "teacher_url": teacher_url,
                "teacher_avatar": teacher_avatar,
                "hours": hours.get_text(" ", strip=True) if hours else "",
                "control": control.get_text(" ", strip=True) if control else "",
                "grade": grade.get_text(" ", strip=True) if grade else "",
                "url": url,
            })
        return result

    def _ajax(self, page_html: str, method: str, args: dict, *, allow_empty=False):
        match = re.search(r'''["']?sesskey["']?\s*[:=]\s*["']?([a-zA-Z0-9]+)''', page_html)
        if not match:
            return None
        payload = [{"index": 0, "methodname": method, "args": args}]
        try:
            response = self._request(
                "POST", f"/lib/ajax/service.php?sesskey={match.group(1)}&info={method}", json=payload
            )
            result = response.json()
            if isinstance(result, list) and result and not result[0].get("error") and "data" in result[0]:
                data = result[0]["data"]
                return True if allow_empty and data is None else data
        except (SDOError, ValueError, KeyError, TypeError):
            return None
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

    def _notification_context(self):
        page = self._get("/message/output/popup/notifications.php")
        soup = BeautifulSoup(page, "html.parser")
        area = soup.select_one('[data-region="notification-area"][data-user-id]')
        if not area or not area["data-user-id"].isdecimal():
            raise SDOError("СДО не показала учётную запись для уведомлений")
        return page, int(area["data-user-id"])

    def notification_snapshot(self, my_html: str) -> dict:
        _, user_id = self._notification_context()
        data = self._ajax(my_html, "message_popup_get_popup_notifications", {"useridto": user_id, "limit": 20, "offset": 0})
        if not isinstance(data, dict) or not isinstance(data.get("unreadcount"), int):
            raise SDOError("СДО не вернула состояние уведомлений")
        result = []
        for item in data.get("notifications", []):
            if not isinstance(item, dict):
                continue
            subject = BeautifulSoup(str(item.get("subject", "Уведомление")), "html.parser").get_text(" ", strip=True)
            url = item.get("contexturl") or item.get("url") or "/message/output/popup/notifications.php"
            try:
                url = safe_url(url)
            except SDOError:
                url = BASE + "/message/output/popup/notifications.php"
            body = BeautifulSoup(
                str(item.get("fullmessagehtml") or item.get("fullmessage") or ""),
                "html.parser",
            ).get_text("\n", strip=True)
            result.append({
                "id": item.get("id"), "read": bool(item.get("read") or item.get("timeread")),
                "title": subject, "time": item.get("timecreated"),
                "url": url, "body": body,
            })
        return {"items": result, "unread": data["unreadcount"]}

    def notifications(self, my_html: str) -> list[dict]:
        return self.notification_snapshot(my_html)["items"]

    def mark_notification_read(self, notification_id: int) -> dict:
        page, user_id = self._notification_context()
        result = self._ajax(page, "core_message_mark_notification_read", {"notificationid": int(notification_id)})
        if not isinstance(result, dict) or result.get("notificationid") != int(notification_id):
            raise SDOError("СДО не подтвердила прочтение уведомления")
        unread = self._ajax(page, "message_popup_get_unread_popup_notification_count", {"useridto": user_id})
        return {"unread": unread if isinstance(unread, int) else None}

    def topics(self, course_url: str, html=None) -> list[dict]:
        parsed = urlparse(safe_url(course_url))
        if parsed.path != "/course/view.php" or not parse_qs(parsed.query).get("id"):
            raise SDOError("Некорректная ссылка на курс")
        soup = BeautifulSoup(self._get(course_url) if html is None else html, "html.parser")
        course = soup.select_one("#region-main .course-content")
        if not course:
            raise SDOError("СДО не вернула список тем этого курса")
        result = []
        seen = set()
        for link in course.select('.sectionname, a[href*="/mod/"]'):
            if link.find_parent(class_="activityinline"):
                continue
            if "sectionname" in link.get("class", []):
                title = link.get_text(" ", strip=True)
                if title:
                    result.append({"title": title, "url": "", "kind": "раздел"})
                continue
            try:
                url = safe_url(link.get("href", "").replace("&amp;", "&"))
            except SDOError:
                continue
            if url in seen:
                continue
            seen.add(url)
            title = " ".join(
                part.strip() for part in link.strings
                if part.strip() and not part.find_parent(class_="accesshide")
            ) or link.get("title") or ""
            if not title:
                parent = link.find_parent(class_="activity")
                title = parent.get_text(" ", strip=True)[:120] if parent else ""
            if title:
                info = link.find_parent(class_="activity")
                status = info.select_one(".automatic-completion-conditions") if info else None
                completion = status.get_text(" ", strip=True) if status else ""
                indicators = re.findall(r"(?:^|\s)(Выполнено|Сделать|Done|To do)\s*:", completion, re.IGNORECASE)
                completed = None if not indicators else all(
                    indicator.casefold() in {"выполнено", "done"} for indicator in indicators
                )
                result.append({
                    "title": title, "url": url, "kind": urlparse(url).path.split("/")[2],
                    "completion": completion, "completed": completed,
                })
        return result

    def course_progress(self, course_url: str) -> dict:
        tracked = [item["completed"] for item in self.topics(course_url) if item.get("completed") is not None]
        return {"completed": sum(tracked), "total": len(tracked)}

    def assignments(self, course_url: str) -> list[dict]:
        parsed = urlparse(safe_url(course_url))
        if parsed.path != "/course/view.php" or not parse_qs(parsed.query).get("id"):
            raise SDOError("Некорректная ссылка на курс")
        soup = BeautifulSoup(self._get(course_url), "html.parser")
        seen = set()
        result = []
        for a in soup.select('a[href*="/mod/assign/view.php"], a[href*="/mod/assignpractice/view.php"]'):
            try:
                url = safe_url(a.get("href", ""))
            except SDOError:
                continue
            if url in seen:
                continue
            seen.add(url)
            try:
                opened = self._request("GET", url)
            except SDOError:
                continue
            if urlparse(opened.url).path not in ("/mod/assign/view.php", "/mod/assignpractice/view.php"):
                continue
            result.append(asdict(Assignment(a.get_text(" ", strip=True) or a.get("title") or "Задание", url)))
        return result


def parse_courses(html: str) -> list[Course]:
    soup = BeautifulSoup(html, "html.parser")
    program_id = None
    for a in soup.select('a[href*="/course/view.php?"]'):
        try:
            candidate = parse_qs(urlparse(safe_url(a.get("href", ""))).query).get("pid", [None])[0]
            if candidate and candidate.isdecimal():
                program_id = candidate
                break
        except SDOError:
            continue
    seen = set()
    courses = []
    for a in soup.select('a[href*="/course/view.php?id="]'):
        try:
            url = safe_url(a.get("href", ""))
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            course_id = int(params["id"][0])
            if program_id and "pid" not in params:
                params["pid"] = [program_id]
                url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
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
            element.get("data-event-id", ""),
        ))
    return result
