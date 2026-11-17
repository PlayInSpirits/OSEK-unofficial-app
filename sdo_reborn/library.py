"""Public webinar catalog linked by the college, with a separate HTTP session."""

import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .client import SDOError

LIBRARY_HOST = "sdo.vgaps.ru"
SEARCH_PATH = "/local/edu/classes/newlib/ajax.php"


def public_url(value, path=None):
    url = urlparse(value)
    if (url.scheme != "https" or url.hostname != LIBRARY_HOST or url.username is not None
            or url.port is not None or path and url.path != path):
        raise SDOError("Ссылка ведёт за пределы электронной библиотеки")
    return value


def public_request(method, url, **options):
    try:
        response = requests.request(method, public_url(url), timeout=20, allow_redirects=False, **options)
        if response.status_code != 200:
            response.close()
            raise SDOError(f"Электронная библиотека вернула HTTP {response.status_code}")
        return response
    except requests.RequestException:
        raise SDOError("Не удалось связаться с электронной библиотекой") from None


class WebinarLibrary:
    def __init__(self, client, project):
        self.client = client
        self.project = project
        self.source = None
        self.search = None
        self.categories = None

    def _discover(self, root):
        link = root.select_one('a[href*="to=lib"]')
        if not link:
            raise SDOError("В аккаунте СДО нет ссылки на электронную библиотеку")
        page = BeautifulSoup(self.client._get(link["href"]), "html.parser")
        iframe = page.select_one('#region-main iframe[src*="sdo.vgaps.ru/library.php"]')
        if not iframe:
            raise SDOError("СДО не вернула каталог вебинаров")
        self.source = public_url(iframe["src"], "/library.php")
        response = public_request("GET", self.source)
        soup = BeautifulSoup(response.text, "html.parser")
        form = soup.select_one("form.search[tpl*='webinar']")
        if not form:
            raise SDOError("Библиотека не вернула поиск вебинаров")
        self.search = public_url(form["action"], SEARCH_PATH)
        match = re.search(r'amd\.init\("#newlib",\s*(.+?)\);', response.text)
        if match:
            try:
                options = json.loads("[" + match[1] + "]")
            except ValueError:
                options = []
            if len(options) >= 4 and isinstance(options[3], str) and re.fullmatch(r"\d+(,\d+)*", options[3]):
                self.categories = options[3]
        if not self.categories:
            raise SDOError("Библиотека не указала категории колледжа")

    def catalog(self, root, page, title):
        if not isinstance(page, int) or not 0 <= page < 100 or not isinstance(title, str) or len(title) > 150:
            raise SDOError("Уточните параметры поиска")
        if not self.categories:
            self._discover(root)
        search = self.search + "&withurl="
        data = {
            "startUrl": "https://" + LIBRARY_HOST + "/library.php", "isadmin": "0",
            "h4_text": "Вебинары", "filters[page]": str(page), "filters[tags]": self.categories,
            "filters[title]": title.strip(), "filters[authors]": "", "filters[add]": "true" if page else "",
        }
        try:
            reply = public_request("POST", search, data=data).json()
        except ValueError:
            raise SDOError("Библиотека вернула неизвестный формат каталога") from None
        if not isinstance(reply, dict) or not isinstance(reply.get("webinars"), list) and not reply.get("empty"):
            raise SDOError("Библиотека не вернула список записей")
        cards = []
        for record in reply.get("webinars") or []:
            identity = str(record.get("id", ""))
            if not identity.isdecimal():
                continue
            thumb = record.get("imgUrl", "")
            try:
                thumb = public_url(urljoin(self.source, thumb)) if thumb else ""
            except SDOError:
                thumb = ""
            query = urlencode({"withurl": "", "id": identity, "type": "webinar", "action": "getOne", "single_link": "1"})
            cards.append({
                "title": BeautifulSoup(str(record.get("title", "")), "html.parser").get_text(" ", strip=True),
                "date": str(record.get("dateview") or ""), "author": str(record.get("author_name") or ""),
                "image": thumb, "url": f"https://{LIBRARY_HOST}/library.php?{query}",
            })
        return {"items": cards, "more": bool(reply.get("hasButton"))}

    def detail(self, url):
        params = parse_qs(urlparse(public_url(url, "/library.php")).query)
        if params.get("type") != ["webinar"] or params.get("action") != ["getOne"]:
            raise SDOError("Адрес не является записью вебинара")
        soup = BeautifulSoup(public_request("GET", url).text, "html.parser")
        main = soup.select_one(".single-row .booklist-main")
        if not main:
            raise SDOError("Библиотека не вернула запись вебинара")
        return {"blocks": [self.project(main, url)]}
