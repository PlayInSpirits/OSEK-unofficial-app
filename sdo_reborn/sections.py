"""Read the student-facing sections without running the site's JavaScript."""

from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString

from .client import SDOError, safe_url


PROGRAM_ACTIONS = {
    "documents": "docs",
    "schedule": "kug",
    "grades": "ezk",
    "official": "notice_official",
}
SECTION_SELECTORS = {
    "documents": "#frontpage-available-course-list .table-responsive",
    "schedule": "#frontpage-available-course-list .programs_schedule",
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


def project_content(node, base):
    if isinstance(node, NavigableString):
        return str(node) if type(node) is NavigableString else ""
    if node.name in IGNORED_TAGS or node.has_attr("hidden"):
        return ""
    if {"sr-only", "accesshide", "course-content"}.intersection(node.get("class", [])):
        return ""
    children = [project_content(child, base) for child in node.children]
    if node.name not in ALLOWED_TAGS:
        return {"tag": "div", "attrs": {}, "children": children}
    attrs = {}
    if node.name == "a" and node.get("href"):
        href = node["href"]
        if not href.startswith("#"):
            try:
                url = safe_url(urljoin(base, href))
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
                attrs["src"] = safe_url(src)
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
    return {"tag": node.name, "attrs": attrs, "children": children}


class StudentSections:
    def __init__(self, client):
        self.client = client
        self.home = None

    def _home(self):
        if self.home is None:
            self.home = BeautifulSoup(self.client._get("/"), "html.parser")
        return self.home

    def programs(self):
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
        blocks = soup.select(SECTION_SELECTORS["material"])
        blocks = [block for block in blocks if not block.find_parent(attrs={"role": "main"})]
        result = []
        for block in blocks:
            frame = block.select_one("iframe[src]")
            if frame and urlparse(url).path == "/mod/nwresource/view.php":
                src = safe_url(urljoin(url, frame["src"]))
                if not urlparse(src).path.startswith("/pluginfile.php/"):
                    raise SDOError("СДО вернула неизвестный адрес файла материала")
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

    def read(self, section, program="", material_url=""):
        if section not in SECTION_SELECTORS:
            raise SDOError("Неизвестный раздел")
        if section in {"material", "webinars"} and material_url:
            url = safe_url(material_url)
        else:
            url = self._section_url(section, program)
        parsed = urlparse(url)
        if section == "material" and not parsed.path.startswith("/mod/"):
            raise SDOError("Эта ссылка не является учебным материалом")
        if section == "webinars" and material_url:
            query = parse_qs(parsed.query)
            if parsed.path != "/" or query.get("action") != ["bbb_calendar"] or set(query) - {"action", "time", "view"}:
                raise SDOError("Некорректная ссылка календаря вебинаров")
        soup = BeautifulSoup(self.client._get(url), "html.parser")
        if section == "material":
            title = soup.select_one("#page-header h1")
            return {
                "title": title.get_text(" ", strip=True) if title else "",
                "blocks": self._material_blocks(soup, url),
            }
        blocks = soup.select(SECTION_SELECTORS[section])
        if section == "webinars":
            for link in soup.select(".calendar-controls a[href]"):
                if not link.get_text(strip=True):
                    description = link.get("title") or link.get("aria-label")
                    if description:
                        link.append(description)
        if not blocks:
            raise SDOError("СДО не вернула содержимое этого раздела")
        return {
            "title": "",
            "blocks": [project_content(block, url) for block in blocks],
        }
