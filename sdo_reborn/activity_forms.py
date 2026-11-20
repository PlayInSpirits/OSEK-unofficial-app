"""Study forms backed by the current Moodle session."""

import json
from copy import copy
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from bs4 import BeautifulSoup
from requests_toolbelt.multipart.encoder import MultipartEncoder

from .client import SDOError, safe_url


UPLOAD_MODULES = {"assign", "assignauto", "assignpractice"}
FORM_TARGETS = {f"/mod/{module}/view.php" for module in UPLOAD_MODULES}
FORM_TARGETS.update({"/mod/quiz/startattempt.php", "/mod/quiz/processattempt.php"})
SUPPORTED_QUESTIONS = {"multichoice", "truefalse", "shortanswer", "numerical", "match", "essay", "description"}


def answer_label(node, form):
    # Moodle may put the caption in a sibling and link it through ARIA
    # instead of emitting a <label for="...">.
    def meaningful(tag):
        return tag and (tag.get_text(" ", strip=True) or tag.select_one("img"))

    label = form.find("label", attrs={"for": node["id"]}) if node.get("id") else None
    if not meaningful(label) and not node.get("id"):
        label = node.find_parent("label")
    if not meaningful(label):
        ids = node.get("aria-labelledby", "").split()
        references = [form.find(id=target) for target in ids]
        references = [tag for tag in references if meaningful(tag)]
        if references:
            return " ".join(tag.get_text(" ", strip=True) for tag in references).strip(), references
        label = node.find_parent("label")
    if not meaningful(label) and node.get("type") in {"radio", "checkbox"}:
        row = node.find_parent(class_=lambda value: value and re.fullmatch(r"r\d+", value))
        if row:
            label = row.select_one(".flex-fill, [data-region='answer-text'], .answertext")
    if meaningful(label):
        return label.get_text(" ", strip=True), [label]
    return node.get("aria-label") or node.get("placeholder") or "Ответ", []


def answer_caption(labels, row, project, url):
    captions = [copy(tag) for tag in labels]
    marker = row.select_one(".answernumber") if row else None
    number = marker.get_text(" ", strip=True) if marker else ""
    for caption in captions:
        for duplicate in caption.select(".answernumber"):
            if not number:
                number = duplicate.get_text(" ", strip=True)
            duplicate.decompose()
    return number, [project(tag, url) for tag in captions]


class ActivityForms:
    def __init__(self, client):
        self.client = client
        self.pending = {}

    def describe(self, soup, url, project):
        module = urlparse(url).path.split("/")[2]
        if module not in UPLOAD_MODULES | {"quiz"}:
            return []
        main = soup.select_one("#region-main")
        if not main:
            return []
        managers = {}
        for script in soup.find_all("script"):
            source = script.get_text()
            for match in re.finditer(r"M\.form_filemanager\.init\(Y,\s*(\{)", source):
                try:
                    state = json.JSONDecoder().raw_decode(source[match.start(1):])[0]
                except ValueError:
                    continue
                managers[str(state.get("itemid", ""))] = state
        rendered = []
        for form in main.select("form[action]"):
            action = urljoin(url, form["action"])
            path = urlparse(action).path
            if path not in FORM_TARGETS or form.get("method", "").lower() != "post":
                continue
            action = safe_url(action)
            if path == "/mod/quiz/startattempt.php" and main.select_one("form input[name='_qf__mod_quiz_preflight_check_form']") and not form.select_one("input[name='_qf__mod_quiz_preflight_check_form']"):
                continue
            buttons = []
            for node in form.select("button, input[type='submit']"):
                if node.has_attr("disabled") or node.get("type", "submit") != "submit":
                    continue
                label = node.get_text(" ", strip=True) if node.name == "button" else node.get("value", "")
                if label and node.get("name") not in {"cancel", "resetbutton"}:
                    buttons.append((label, node.get("name"), node.get("value", label)))
            if not buttons:
                continue
            hidden = {x["name"]: x.get("value", "") for x in form.select('input[type="hidden"][name]:not([disabled])')}
            if not hidden.get("sesskey"):
                continue
            upload = next((managers[value] for field, value in hidden.items() if field != "sesskey" and value in managers and ("files" in field or "filemanager" in field)), None)
            if form.select_one(".filemanager") and not upload:
                continue
            questions = form.select(".que")
            if any(not SUPPORTED_QUESTIONS.intersection(q.get("class", [])) for q in questions):
                continue
            groups = []
            allowed = {}
            for area in questions or [form]:
                widgets = []
                for node in area.select("input[name],textarea[name],select[name]"):
                    kind = node.get("type", "text") if node.name == "input" else node.name
                    if node.has_attr("disabled") or kind not in {"text", "password", "number", "checkbox", "radio", "textarea", "select"}:
                        continue
                    name = node["name"]
                    label, labels = answer_label(node, form)
                    options = [{"value": opt.get("value", ""), "label": opt.get_text(" ", strip=True)} for opt in node.select("option:not([disabled])")]
                    value = node.get_text() if kind == "textarea" else node.get("value", "1" if kind in {"checkbox", "radio"} else "")
                    if kind == "select":
                        selected = node.select_one("option[selected]") or node.select_one("option")
                        value = selected.get("value", "") if selected else ""
                    number, caption = ("", [])
                    if kind in {"radio", "checkbox"}:
                        row = node.find_parent(class_=lambda value: value and re.fullmatch(r"r\d+", value))
                        number, caption = answer_caption(labels, row, project, url)
                    widgets.append({
                        "name": name, "kind": kind, "label": label, "value": value,
                        "checked": node.has_attr("checked"), "options": options,
                        "caption": caption, "number": number,
                    })
                    if kind == "select":
                        allowed.setdefault(name, set()).update(option["value"] for option in options)
                    elif kind in {"radio", "checkbox"}:
                        allowed.setdefault(name, set()).add(value)
                    else:
                        allowed[name] = None
                question = area.select_one(".qtext") if questions else None
                groups.append({"content": project(question, url) if question else "", "widgets": widgets, "question": bool(questions)})
            token = uuid4().hex
            self.pending[token] = (url, action, hidden, upload, allowed, buttons)
            while len(self.pending) > 35:
                del self.pending[next(iter(self.pending))]
            selected_files = []
            if upload:
                listing = upload.get("list")
                if isinstance(listing, list):
                    selected_files = [str(row.get("filename")) for row in listing if isinstance(row, dict) and row.get("filename") not in {None, "."}]
            rendered.append({
                "token": token, "groups": groups, "buttons": [item[0] for item in buttons],
                "confirm": bool(hidden.get("finishattempt") == "1" or path.endswith("startattempt.php")),
                "upload": bool(upload), "maxFiles": int(upload.get("maxfiles", 0)) if upload else 0,
                "existingFiles": selected_files, "existingCount": int(upload.get("filecount", 0)) if upload else 0,
            })
        return rendered

    def upload_limit(self, token):
        entry = self.pending.get(token)
        upload = entry[3] if entry else None
        if not upload:
            raise SDOError("Форма загрузки больше недоступна. Откройте задание снова.")
        maximum = int(upload.get("maxfiles", 0))
        count = int(upload.get("filecount", 0))
        return maximum - count if maximum > 0 else None

    def _upload(self, path, settings, sesskey):
        picker = settings.get("filepicker") or {}
        repository = next((row for row in picker.get("repositories", {}).values() if row.get("type") == "upload"), None)
        if not repository:
            raise SDOError("СДО не разрешает загрузку файла с компьютера")
        max_bytes = int(settings.get("maxbytes", 0))
        if not path.is_file() or max_bytes > 0 and path.stat().st_size > max_bytes:
            raise SDOError("Файл недоступен или превышает лимит СДО")
        if not path.stat().st_size:
            raise SDOError("Выбранный файл пуст")
        types = settings.get("accepted_types", [])
        accepted = types if isinstance(types, list) else [types]
        fields = [
            ("repo_id", str(repository["id"])), ("env", "filemanager"),
            ("savepath", str(settings.get("path") or "/")), ("sesskey", sesskey),
            ("client_id", str(settings["client_id"])), ("itemid", str(settings["itemid"])),
            ("ctx_id", str(settings["context"]["id"])), ("maxbytes", str(max_bytes)),
            ("areamaxbytes", str(settings.get("areamaxbytes") or -1)),
            ("title", ""), ("author", str(picker.get("author") or settings.get("author") or "")),
            ("license", str(picker.get("defaultlicense") or settings.get("defaultlicense") or "")),
        ]
        fields.extend(("accepted_types[]", str(kind)) for kind in accepted)
        with path.open("rb") as stream:
            payload = MultipartEncoder(fields=fields + [("repo_upload_file", (path.name, stream, "application/octet-stream"))])
            response = self.client._request("POST", "/repository/repository_ajax.php?action=upload", data=payload, headers={"Content-Type": payload.content_type})
        try:
            data = response.json()
        except ValueError:
            raise SDOError("СДО не подтвердила загрузку файла") from None
        if not isinstance(data, dict) or data.get("error") or data.get("event") == "fileexists" or not data.get("file"):
            raise SDOError("СДО отклонила файл. Проверьте формат, лимит и имя файла.")

    def submit(self, token, button, values, paths):
        entry = self.pending.pop(token, None)
        if not entry:
            raise SDOError("Форма устарела. Откройте её заново.")
        url, action, hidden, upload, allowed, buttons = entry
        if not isinstance(button, int) or not 0 <= button < len(buttons):
            raise SDOError("СДО не предоставила эту кнопку")
        fields = hidden.copy()
        for key, value in values.items():
            if not isinstance(value, str) or len(value) > 250_000 or key not in allowed or allowed[key] is not None and value not in allowed[key]:
                raise SDOError("Недопустимое значение поля")
            fields[key] = value
        if paths:
            if not upload:
                raise SDOError("В этой форме нельзя прикреплять файлы")
            max_files = int(upload.get("maxfiles", 0))
            if max_files > 0 and len(paths) + int(upload.get("filecount", 0)) > max_files:
                raise SDOError("Слишком много файлов для этой формы")
            for path in paths:
                self._upload(Path(path), upload, hidden["sesskey"])
        _, name, value = buttons[button]
        if name:
            fields[name] = value
        response = self.client._request("POST", action, data=fields)
        soup = BeautifulSoup(response.text, "html.parser")
        error = soup.select_one("#region-main .alert-danger, #region-main .error, #region-main .invalid-feedback:not([hidden])")
        if error and error.get_text(" ", strip=True):
            raise SDOError(error.get_text(" ", strip=True)[:350])
        if not self.client._logged_in(response.text):
            raise SDOError("СДО не подтвердила действие. Проверьте состояние задания перед повтором.")
        if upload and paths:
            verified = self.client._get(url)
            checked = BeautifulSoup(verified, "html.parser")
            evidence = checked.select_one("#region-main")
            text = [evidence.get_text(" ", strip=True)] if evidence else []
            for script in checked.select("script"):
                for match in re.finditer(r"M\.form_filemanager\.init\(Y,\s*(\{)", script.get_text()):
                    state = json.JSONDecoder().raw_decode(script.get_text()[match.start(1):])[0]
                    text.append(json.dumps(state.get("list", []), ensure_ascii=False))
            if not all(any(Path(path).name in signal for signal in text) for path in paths):
                raise SDOError("СДО приняла запрос, но не показала сохранённые файлы. Проверьте состояние задания перед повтором.")
        return response.url, soup
