"""Read authenticated files and render PDF pages without browser navigation."""

import base64
import io
from urllib.parse import parse_qs, urlparse

import pypdfium2
from PIL import Image, UnidentifiedImageError
from requests import RequestException

from .client import SDOError, safe_url
from .library import LIBRARY_HOST, public_request, public_url


def data_url(content, mime):
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


class StudentFiles:
    def __init__(self, client):
        self.client = client
        self.pdf_url = ""
        self.pdf_data = b""

    def _download(self, url, limit):
        for attempt in range(2):
            try:
                with self.client._request("GET", url, stream=True) as response:
                    if urlparse(response.url).path != "/login/index.php":
                        result = bytearray()
                        for part in response.iter_content(chunk_size=65536):
                            if len(result) + len(part) > limit:
                                raise SDOError("Файл слишком большой для встроенного просмотра")
                            result.extend(part)
                        return bytes(result)
            except RequestException:
                raise SDOError("Загрузка файла прервалась") from None
            if attempt == 0 and self.client.username and self.client._password:
                self.client.login(self.client.username, self.client._password)
        raise SDOError("Не удалось восстановить доступ к файлу СДО")

    def image(self, url):
        url = safe_url(url)
        if not urlparse(url).path.startswith(("/pluginfile.php/", "/theme/")):
            raise SDOError("Адрес изображения СДО не поддерживается")
        raw = self._download(url, 12 * 1024 * 1024)
        try:
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.width * picture.height > 24_000_000:
                    raise SDOError("Разрешение изображения превышает предел просмотра")
                mime = Image.MIME.get(picture.format)
                if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}:
                    raise SDOError("Формат изображения не поддерживается")
                picture.verify()
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
            raise SDOError("СДО не вернула читаемое изображение") from None
        return data_url(raw, mime)

    def _load_pdf(self, url):
        parsed = urlparse(url)
        is_public = parsed.hostname == LIBRARY_HOST
        url = public_url(url) if is_public else safe_url(url)
        if is_public and not (parsed.path.startswith("/pluginfile.php/") and parsed.path.lower().endswith(".pdf")):
            raise SDOError("Ссылка не является презентацией библиотеки")
        if parsed.path not in {"/report/studybook/getpdf.php", "/mod/gbooks/view.php"} and not (
            parsed.path.startswith("/pluginfile.php/") and parsed.path.lower().endswith(".pdf")
        ):
            raise SDOError("Адрес PDF не поддерживается")
        if parsed.path == "/mod/gbooks/view.php" and "pdf" not in parse_qs(parsed.query):
            raise SDOError("СДО не указала PDF книги")
        if self.pdf_url != url:
            limit = 35 * 1024 * 1024
            if is_public:
                with public_request("GET", url, stream=True) as response:
                    buffer = bytearray()
                    try:
                        for part in response.iter_content(chunk_size=65536):
                            if len(buffer) + len(part) > limit:
                                raise SDOError("PDF библиотеки превышает предел просмотра")
                            buffer.extend(part)
                    except RequestException:
                        raise SDOError("Загрузка презентации прервалась") from None
                content = bytes(buffer)
            else:
                content = self._download(url, limit)
            if not content.startswith(b"%PDF-"):
                raise SDOError("СДО вернула ответ без PDF")
            self.pdf_url, self.pdf_data = url, content

    def pdf_document(self, url):
        self._load_pdf(url)
        return self.pdf_data

    def pdf_text(self, url):
        self._load_pdf(url)
        parts = []
        size = 0
        try:
            with pypdfium2.PdfDocument(self.pdf_data) as document:
                for index, page in enumerate(document, 1):
                    try:
                        layer = page.get_textpage()
                        try:
                            content = layer.get_text_bounded().strip()
                        finally:
                            layer.close()
                    finally:
                        page.close()
                    if content:
                        size += len(content)
                        if size > 600000:
                            raise SDOError("В PDF слишком много текста для одной выжимки.")
                        parts.append(f"[Страница {index}]\n{content}")
        except pypdfium2.PdfiumError:
            raise SDOError("Не удалось извлечь текст PDF.") from None
        if not parts:
            raise SDOError("В PDF нет текстового слоя. Для сканов нужно распознавание.")
        return "\n\n".join(parts)

    def pdf_page(self, url, index):
        return render_pdf_page(self.pdf_document(url), index)


def render_pdf_page(pdf, index):
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise SDOError("Неверный номер страницы")
    try:
        with pypdfium2.PdfDocument(pdf) as document:
            total = len(document)
            if index >= total:
                raise SDOError("Такой страницы в PDF нет")
            page = document[index]
            try:
                scale = min(2, 2400 / max(page.get_width(), page.get_height()))
                bitmap = page.render(scale=scale)
                try:
                    png = io.BytesIO()
                    bitmap.to_pil().save(png, format="PNG")
                finally:
                    bitmap.close()
            finally:
                page.close()
    except pypdfium2.PdfiumError:
        raise SDOError("Не удалось прочитать PDF СДО") from None
    return {"image": data_url(png.getvalue(), "image/png"), "total": total}
