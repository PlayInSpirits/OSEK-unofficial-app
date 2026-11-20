"""Read the student-facing sections without running the site's JavaScript."""

import re
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString

from .activity_forms import ActivityForms, UPLOAD_MODULES
from .client import SDOError, parse_events, safe_url
from .library import LIBRARY_HOST, WebinarLibrary, public_url


PROGRAM_ACTIONS = {
    "documents": "docs",
    "schedule": "kug",
    "grades": "ezk",
    "official": "notice_official",
}
SECTION_SELECTORS = {
    "documents": "#frontpage-available-course-list .table-responsive",
    "schedule": "#frontpage-available-course-list .programs_schedule table",
    "grades": "#frontpage-available-course-list .esd_page",
    "official": "#frontpage-available-course-list",
    "webinars": ".maincalendar",
    "news": ".orgs_news",
    "profile": ".userprofile",
    "material": "#region-main [role='main']",
}
ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "caption", "code", "dd", "div", "dl",
    "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5",
    "h6", "hr", "i", "img", "li", "ol", "p", "pre", "section", "small",
    "source", "span", "strong", "sub", "sup", "table", "tbody", "td",
    "th", "thead", "tr", "u", "ul", "video", "audio",
}
IGNORED_TAGS = {
    "script", "style", "template", "noscript", "form", "input", "button",
    "iframe", "object", "embed", "svg", "select", "textarea",
}
MEDIA_HOSTS = {"sdo.i-spo.ru", "media2.sdo.support"}
SCHEDULE_STYLES = {"backwhite", "backgray"}
INLINE_IMAGE = re.compile(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\r\n]+")


def project_content(node, base):
    if isinstance(node, NavigableString):
        return str(node) if type(node) is NavigableString else ""
    if node.name in IGNORED_TAGS or node.has_attr("hidden"):
        return ""
    classes = set(node.get("class", []))
    if {"sr-only", "accesshide", "course-content"}.intersection(classes):
        return ""
    if "cont_video" in classes:
        # The remote player supplies its own buttons and timers in sibling nodes.
        # The native WebView player already renders those controls.
        video = node.find("video", recursive=False)
        if video is not None:
            return project_content(video, base)
    if {"mob_name", "name-hide"} <= classes and node.find_parent(class_="mvideo_container"):
        return ""
    children = [project_content(child, base) for child in node.children]
    if node.name not in ALLOWED_TAGS:
        return {"tag": "div", "attrs": {}, "children": children}
    attrs = {}
    if node.name == "a" and node.get("href"):
        href = node["href"]
        if not href.startswith("#"):
            try:
                url = urljoin(base, href)
                if urlparse(base).hostname == LIBRARY_HOST and urlparse(url).hostname == LIBRARY_HOST:
                    public_url(url)
                else:
                    safe_url(url)
                parsed = urlparse(url)
                if "sesskey" not in parse_qs(parsed.query) and not parsed.path.startswith("/login/"):
                    attrs["href"] = url
            except (SDOError, ValueError):
                attrs.clear()
    if node.name in {"img", "video", "audio", "source"} and node.get("src"):
        try:
            src = urljoin(base, node["src"])
            parsed = urlparse(src)
            if node.name == "img":
                if len(src) <= 8_000_000 and INLINE_IMAGE.fullmatch(src):
                    attrs["src"] = src
                else:
                    attrs["src"] = public_url(src) if parsed.hostname == LIBRARY_HOST and urlparse(base).hostname == LIBRARY_HOST else safe_url(src)
            elif parsed.scheme == "https" and parsed.hostname in MEDIA_HOSTS and parsed.port is None:
                attrs["src"] = src
            else:
                return ""
        except (SDOError, ValueError):
            return ""
    if node.name == "img":
        attrs["alt"] = node.get("alt", "")
    if node.name in {"td", "th"}:
        for attr in ("colspan", "rowspan"):
            value = node.get(attr, "")
            if value.isdecimal() and 1 < int(value) <= 100:
                attrs[attr] = value
    if node.name == "table" and "schedule" in node.get("class", []):
        attrs["class"] = "schedule-grid"
    schedule_classes = SCHEDULE_STYLES.intersection(node.get("class", []))
    if schedule_classes:
        attrs["class"] = " ".join(sorted(schedule_classes))
    return {"tag": node.name, "attrs": attrs, "children": children}


class StudentSections:
    def __init__(self, client, files=None):
        self.client = client
        self.files = files
        self.home = None
        self._courses = {}
        self._curriculum = {}
        self._programs = None
        self.calendar_html = ""
        self.meeting_months = {}
        self.meeting_subscriptions = {}
        self.forms = ActivityForms(client)
        self.library = WebinarLibrary(client, project_content)

    def curriculum(self, program):
        cached = self._curriculum.get(program)
        if cached and time.monotonic() - cached[0] < 120:
            return cached[1]
        items = self.client.curriculum(program)
        self._curriculum[program] = (time.monotonic(), items)
        return items

    def _course_data(self, url):
        cached = self._courses.get(url)
        if cached and time.monotonic() - cached[0] < 90:
            return cached[1]
        html = self.client._get(url)
        items = self.client.topics(url, html)
        soup = BeautifulSoup(html, "html.parser")
        lead = soup.select(
            "#region-main .course-content .section-summary .summarytext,"
            "#region-main .course-content .activityinline .description-inner"
        )
        overview = [project_content(node, url) for node in lead if node.get_text(strip=True)]
        data = {"items": items, "overview": overview}
        self._courses[url] = (time.monotonic(), data)
        if len(self._courses) > 50:
            del self._courses[next(iter(self._courses))]
        return data

    def course_progress(self, url):
        items = self._course_data(url)["items"]
        tracked = [item["completed"] for item in items if item.get("completed") is not None]
        return {"completed": sum(tracked), "total": len(tracked)}

    def course(self, url):
        return self._course_data(url)

    def recordings(self, page, title):
        return self.library.catalog(self._home(), page, title)

    def student_contact(self, final_url):
        query = parse_qs(urlparse(final_url).query)
        user_id = query.get("id", [""])[0]
        if not user_id.isdecimal():
            raise SDOError("СДО не указала получателя сообщения")
        messages_html, me = self.client._message_context()
        address = {"referenceuserid": me, "userids": [int(user_id)], "includeprivacyinfo": True}
        members = self.client._ajax(messages_html, "core_message_get_member_info", address)
        if not isinstance(members, list) or not members:
            raise SDOError("СДО не показала получателя для этого курса. Можно поискать преподавателя в разделе «Сообщения».")
        person = members[0]
        if int(person.get("id", 0)) != int(user_id):
            raise SDOError("СДО вернула другой профиль")
        thread = self.client._ajax(messages_html, "core_message_get_conversation_between_users", {"userid": me, "otheruserid": int(user_id)})
        avatar = person.get("profileimageurl") or person.get("profileimageurlsmall") or ""
        if avatar:
            try:
                avatar = safe_url(avatar)
            except SDOError:
                avatar = ""
        return {
            "name": person["fullname"], "userId": int(user_id),
            "id": thread.get("id") if isinstance(thread, dict) else None,
            "canMessage": not person.get("isblocked") and person.get("canmessage") not in (False, 0),
            "avatar": avatar,
        }

    def _material(self, url, soup):
        title = soup.select_one("#page-header h1")
        module = urlparse(url).path.split("/")[2]
        forms = self.forms.describe(soup, url, project_content)
        blocks = self._material_blocks(soup, url)
        if module in UPLOAD_MODULES:
            for form in soup.select("#region-main form[method='get'][action]"):
                action = urlparse(urljoin(url, form["action"]))
                if action.path != f"/mod/{module}/view.php":
                    continue
                fields = {field["name"]: field.get("value", "") for field in form.select("input[type='hidden'][name]")}
                if fields.get("action") != "editsubmission":
                    continue
                button = form.select_one("button, input[type='submit']")
                if button:
                    label = button.get_text(" ", strip=True) or button.get("value", "Редактировать ответ")
                    href = safe_url(urlunparse(action._replace(query=urlencode(fields))))
                    blocks.append({"tag": "a", "attrs": {"href": href}, "children": [label]})
        interactive = module in UPLOAD_MODULES | {"quiz", "askquest", "forum"}
        return {
            "title": title.get_text(" ", strip=True) if title else "",
            "blocks": blocks, "forms": forms, "url": url,
            "notice": "СДО не предоставила поддерживаемую форму для этого экрана." if interactive and not forms else "",
        }

    def submit_activity(self, token, button, values, paths):
        url, soup = self.forms.submit(token, button, values, paths)
        return self._material(url, soup)

    def _home(self):
        if self.home is None:
            self.home = BeautifulSoup(self.client._get("/"), "html.parser")
        return self.home

    def programs(self):
        if self._programs is not None:
            return self._programs
        rows = []
        seen = set()
        for item in self._home().select(".side-nav .myprograms"):
            label = item.select_one(".sidenav-title")
            for link in item.select("a[href]"):
                url = urlparse(link["href"])
                query = parse_qs(url.query)
                pid = query.get("pid", [""])[0]
                if url.path == "/" and set(query) == {"pid"} and pid.isdecimal() and pid not in seen:
                    seen.add(pid)
                    rows.append({"id": pid, "label": label.get_text(" ", strip=True) if label else "", "active": "active" in link.get("class", [])})
        self._programs = rows
        return rows

    def _section_url(self, section, program):
        if section == "profile":
            link = self._home().select_one('.usermenu a[href*="/user/profile.php"]')
        elif section == "webinars":
            link = self._home().select_one('a[href*="action=bbb_calendar"]')
        elif section == "news":
            link = self._home().select_one('a[href*="to=news"]')
        else:
            action = PROGRAM_ACTIONS.get(section)
            if not action or not isinstance(program, str) or not program.isdecimal():
                raise SDOError("Выберите учебную программу")
            links = self._home().select(f'.side-nav a[href*="action={action}"][href*="pid="]')
            link = next((entry for entry in links if parse_qs(urlparse(entry["href"]).query).get("pid") == [program]), None)
        if link is None:
            raise SDOError("СДО не показала ссылку на этот раздел для текущей учётной записи")
        return safe_url(link["href"])

    def _material_blocks(self, soup, url):
        if urlparse(url).path == "/mod/gbooks/view.php":
            book = soup.select_one("#wowbook")
            pages = book.select('img[src*="/mod_gbooks/pages/"]') if book else []
            if not pages:
                raise SDOError("СДО не вернула страницы электронной книги")
            for number, image in enumerate(pages, 1):
                image["alt"] = f"Страница {number}"
            pdf = book.select_one('a[href*="pdf="]')
            blocks = [project_content(pdf, url)] if pdf and pdf.get_text(strip=True) else []
            blocks.extend(project_content(image, url) for image in pages)
            return blocks
        blocks = soup.select(SECTION_SELECTORS["material"])
        blocks = [block for block in blocks if not block.find_parent(attrs={"role": "main"})]
        result = []
        for block in blocks:
            frame = block.select_one("iframe[src]")
            if frame and urlparse(url).path == "/mod/nwresource/view.php":
                src = safe_url(urljoin(url, frame["src"]))
                if not urlparse(src).path.startswith("/pluginfile.php/"):
                    raise SDOError("СДО вернула неизвестный адрес файла материала")
                if urlparse(src).path.lower().endswith(".pdf"):
                    result.append({"tag": "a", "attrs": {"href": src}, "children": ["Просмотреть PDF"]})
                    continue
                response = self.client._request("GET", src)
                mime = response.headers.get("Content-Type", "").split(";")[0].strip()
                if mime != "text/html" or urlparse(response.url).path == "/login/index.php":
                    raise SDOError("СДО не вернула HTML содержимое материала")
                document = BeautifulSoup(response.text, "html.parser")
                if document.body is None:
                    raise SDOError("HTML файл материала не содержит текста для отображения")
                result.append(project_content(document.body, response.url))
            else:
                result.append(project_content(block, url))
        if not result:
            raise SDOError("СДО не вернула содержимое учебного материала")
        return result

    def _profile(self, soup, url):
        account = self.client.profile(str(soup))
        photo = soup.select_one(".userprofile img.userpicture[src]")
        account["avatar"] = ""
        if photo and self.files:
            try:
                account["avatar"] = self.files.image(urljoin(url, photo["src"]))
            except SDOError:
                account["avatar"] = ""
        fields = {}
        labels = soup.select(".userprofile .profile-properties-label")
        values = soup.select(".userprofile .profile-properties-content")
        for label, value in zip(labels, values):
            fields.setdefault(label.get_text(" ", strip=True).rstrip(":"), value.get_text(" ", strip=True))
        return {
            "blocks": [], "identity": account,
            "fields": [{"label": key, "value": val} for key, val in fields.items() if key and val],
            "programs": self.programs(),
        }

    def _webinars(self, soup, url):
        calendar = soup.select_one(".maincalendar")
        if not calendar:
            raise SDOError("СДО не вернула календарь вебинаров")
        month = calendar.select_one(".calendar-controls h2")
        controls = []
        for key, label in (("previous", "← Предыдущий месяц"), ("next", "Следующий месяц →")):
            link = calendar.select_one(f".calendar-controls a.{key}[href]")
            if link:
                controls.append({"title": label, "url": safe_url(urljoin(url, link["href"]))})
        events = {}
        for link in calendar.select("a[data-event-id]"):
            identity = link["data-event-id"]
            day = link.find_parent("td")
            if not identity.isdecimal() or not day or identity in events:
                continue
            date = day.get("data-day-timestamp", "")
            time = link.parent.get_text(" ", strip=True).split(" ", 1)[0]
            events[identity] = {
                "id": identity, "title": link.get_text(" ", strip=True),
                "date": int(date) if date.isdecimal() else None,
                "time": time if len(time) == 5 and time[2] == ":" else "",
            }
        return {
            "blocks": [], "month": month.get_text(" ", strip=True) if month else "",
            "controls": controls, "events": list(events.values()),
        }

    def _event(self, identity):
        if not isinstance(identity, str) or not identity.isdecimal():
            raise SDOError("СДО не указала идентификатор события")
        if not self.calendar_html:
            self.calendar_html = self.client._get("/calendar/view.php?view=upcoming")
        data = self.client._ajax(self.calendar_html, "core_calendar_get_calendar_event_by_id", {"eventid": int(identity)})
        event = data.get("event") if isinstance(data, dict) else None
        if not event:
            self.calendar_html = ""
            raise SDOError("СДО не вернула сведения об этом событии")
        blocks = []
        for label, key in (("Дата и время", "formattedtime"), ("Место", "formattedlocation")):
            if event.get(key):
                value = BeautifulSoup(event[key], "html.parser").get_text(" ", strip=True)
                blocks.append(self._event_fact(label, value.replace(" ,", ",")))
        description = BeautifulSoup(event.get("description") or "", "html.parser")
        subscription = None
        if event.get("eventtype") == "bbbevents":
            try:
                facts, subscription = self._meeting_facts(identity, event.get("timestart"))
                blocks.extend(facts)
            except SDOError:
                blocks.append(self._event_fact("Сведения о встрече", "Не удалось загрузить карточку встречи из СДО."))
        if description.get_text(strip=True).casefold() not in {"ввв событие", "bbb событие"}:
            for link in description.select('a[href*="calendar/view.php"]'):
                link.unwrap()
            if description.get_text(strip=True) or description.select_one("img, video, audio"):
                blocks.append(project_content(description, "https://sdo.i-spo.ru/calendar/"))
        return {"title": event["name"], "blocks": blocks, "subscription": subscription}

    @staticmethod
    def _event_fact(label, value):
        return {"tag": "p", "attrs": {}, "children": [
            {"tag": "strong", "attrs": {}, "children": [label + ": "]}, value,
        ]}

    def _meeting_facts(self, identity, timestamp):
        unavailable = self._event_fact("Сведения о встрече", "СДО не опубликовала карточку и материалы для этого занятия.")
        if not isinstance(timestamp, int) or timestamp <= 0:
            return [unavailable], None
        local = datetime.fromtimestamp(timestamp, timezone(timedelta(hours=3)))
        month = local.year, local.month
        if month not in self.meeting_months:
            url = f"/?action=bbb_calendar&view=month&time={timestamp}"
            soup = BeautifulSoup(self.client._get(url), "html.parser")
            self.meeting_months[month] = {
                link["data-event-id"]: link["href"]
                for link in soup.select(".maincalendar a[data-event-id][href]")
            }
        target = self.meeting_months[month].get(identity)
        if not target:
            return [unavailable], None
        target = safe_url(target)
        parsed = urlparse(target)
        params = parse_qs(parsed.query)
        if parsed.path != "/" or params.get("action") != ["bbb_calendar"] or not params.get("id"):
            return [unavailable], None
        page = BeautifulSoup(self.client._get(target), "html.parser")
        facts = []
        for label, selector in (
            ("Курс или организация", "#region-main .course__name"),
            ("Преподаватель", "#region-main .teacher__name"),
            ("Статус", "#region-main .bbb__calendar-message-block"),
        ):
            node = page.select_one(selector)
            if node and node.get_text(strip=True):
                facts.append(self._event_fact(label, node.get_text(" ", strip=True)))
        subscription = self._subscription_button(page)
        if subscription:
            self.meeting_subscriptions[identity] = target
        else:
            self.meeting_subscriptions.pop(identity, None)
        return facts or [unavailable], subscription[2] if subscription else None

    @staticmethod
    def _subscription_button(page):
        button = page.select_one("#region-main #subscribe_button[eventid][user]")
        if not button or button.has_attr("hidden") or "display:none" in re.sub(r"\s+", "", button.get("style", "")).lower():
            return None
        event_id, user_id = button["eventid"], button["user"]
        label = button.get_text(" ", strip=True).casefold()
        if not (event_id.isdecimal() and user_id.isdecimal() and label in {"подписаться", "отписаться"}):
            return None
        return event_id, user_id, label == "отписаться"

    def set_meeting_subscription(self, identity, subscribed):
        if not isinstance(identity, str) or not isinstance(subscribed, bool):
            raise SDOError("Некорректный запрос подписки")
        target = self.meeting_subscriptions.get(identity)
        if not target:
            raise SDOError("Сначала откройте карточку мероприятия")
        button = self._subscription_button(BeautifulSoup(self.client._get(target), "html.parser"))
        if not button:
            raise SDOError("СДО больше не предлагает подписку на это мероприятие")
        event_id, account, current = button
        _, user_id = self.client._message_context()
        if int(account) != user_id:
            raise SDOError("СДО вернула другой аккаунт для подписки")
        if current != subscribed:
            with self.client._request(
                "POST", "/local/bbbevents/ajax.php",
                data={"action": "subscribe", "eventid": event_id, "userid": account},
                headers={"X-Requested-With": "XMLHttpRequest"},
            ) as response:
                if response.text.strip() != ("1" if subscribed else "0"):
                    raise SDOError("СДО не подтвердила подписку. Откройте мероприятие заново.")
        return {"subscribed": subscribed}

    def read(self, section, program="", material_url=""):
        if section == "teacher":
            url = safe_url(material_url)
            if urlparse(url).path != "/user/profile.php":
                raise SDOError("Ссылка на профиль преподавателя не поддерживается")
            contact = self.student_contact(url)
            return {"title": contact["name"], "contact": contact, "blocks": []}
        if section == "event":
            return self._event(material_url)
        if section == "recording":
            return self.library.detail(material_url)
        if section == "calendar_day":
            parsed = urlparse(safe_url(material_url))
            query = parse_qs(parsed.query)
            if parsed.path != "/calendar/view.php" or query.get("view") != ["day"]:
                raise SDOError("СДО не указала день календаря")
            html = self.client._get(material_url)
            soup = BeautifulSoup(html, "html.parser")
            heading = soup.select_one(".calendar-controls h2, #region-main h2")
            return {
                "title": heading.get_text(" ", strip=True) if heading else "События дня",
                "blocks": [], "events": [asdict(item) for item in parse_events(html)],
            }
        if section not in SECTION_SELECTORS:
            raise SDOError("Неизвестный раздел")
        if section in {"material", "webinars"} and material_url:
            url = safe_url(material_url)
        else:
            url = self._section_url(section, program)
        parsed = urlparse(url)
        if section == "material" and not parsed.path.startswith(("/mod/", "/message/index.php")):
            raise SDOError("Эта ссылка не является учебным материалом")
        if section == "webinars" and material_url:
            query = parse_qs(parsed.query)
            if parsed.path != "/" or query.get("action") != ["bbb_calendar"] or set(query) - {"action", "time", "view"}:
                raise SDOError("Некорректная ссылка календаря вебинаров")
        if section == "material":
            response = self.client._authenticated_response(url, headers={"Cache-Control": "no-cache, no-store"})
            soup = BeautifulSoup(response.text, "html.parser")
            if urlparse(response.url).path == "/message/index.php":
                return {"blocks": [], "contact": self.student_contact(response.url)}
            return self._material(response.url, soup)
        soup = BeautifulSoup(self.client._get(url), "html.parser")
        if section == "profile":
            return self._profile(soup, url)
        if section == "webinars":
            return self._webinars(soup, url)
        blocks = soup.select(SECTION_SELECTORS[section])
        if section == "documents":
            for block in blocks:
                for table in block.select("table"):
                    header = table.select_one("thead tr")
                    if not header:
                        continue
                    names = [cell.get_text(" ", strip=True) for cell in header.find_all(["th", "td"], recursive=False)]
                    if "Квитанции" in names and "Статус оплаты" in names:
                        keep = names.index("Статус оплаты") + 1
                        for row in table.select("tr"):
                            for cell in row.find_all(["td", "th"], recursive=False)[keep:]:
                                cell.decompose()
        if not blocks:
            raise SDOError("СДО не вернула содержимое этого раздела")
        return {
            "title": "",
            "blocks": [project_content(block, url) for block in blocks],
        }
