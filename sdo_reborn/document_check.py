"""Text extraction for the online document check."""

from dataclasses import dataclass
from pathlib import Path
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import pypdfium2


MAX_FILE = 80 * 1024 * 1024
MAX_TEXT = 2_000_000
WORDS = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*", re.UNICODE)
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocumentError(Exception):
    """A local file or text analysis error suitable for the UI."""


@dataclass(frozen=True)
class Document:
    path: Path
    size: int
    parts: tuple[str, ...]
    paginated: bool

    def metadata(self):
        return {"name": self.path.name, "path": self.path.as_posix(), "size": self.size}


def stamp(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def read_document(filename):
    path = Path(filename).resolve(strict=True)
    before = path.stat()
    size = before.st_size
    if not path.is_file() or not 0 < size <= MAX_FILE:
        raise DocumentError("Выберите непустой файл размером до 80 МБ.")
    ext = path.suffix.lower()
    paginated = ext == ".pdf"
    if ext in {".txt", ".md"}:
        with path.open("rb") as stream:
            raw = stream.read(MAX_TEXT * 4 + 1)
        if len(raw) > MAX_TEXT * 4:
            raise DocumentError("В документе слишком много текста для проверки.")
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            parts = (raw.decode("utf-16"),)
        else:
            try:
                parts = (raw.decode("utf-8-sig"),)
            except UnicodeDecodeError:
                parts = (raw.decode("cp1251"),)
    elif ext == ".docx":
        try:
            with ZipFile(path) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > MAX_TEXT * 8:
                    raise DocumentError("Разметка DOCX превышает предел локальной проверки.")
                xml = archive.read(info)
            if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", xml, re.IGNORECASE):
                raise DocumentError("DOCX содержит неподдерживаемую разметку.")
            tree = ElementTree.fromstring(xml)
            parts = ("\n".join(
                "".join(node.text or "" for node in paragraph.iter(WORD_NS + "t"))
                for paragraph in tree.iter(WORD_NS + "p")
            ),)
        except (BadZipFile, KeyError, ElementTree.ParseError):
            raise DocumentError("Не удалось прочитать документ DOCX.") from None
    elif paginated:
        pages = []
        length = 0
        try:
            with pypdfium2.PdfDocument(path) as pdf:
                if len(pdf) > 500:
                    raise DocumentError("Для локальной проверки доступно до 500 страниц PDF.")
                for page in pdf:
                    try:
                        text_page = page.get_textpage()
                        try:
                            text = text_page.get_text_bounded()
                        finally:
                            text_page.close()
                    finally:
                        page.close()
                    length += len(text)
                    if length > MAX_TEXT:
                        raise DocumentError("В PDF слишком много текста для проверки.")
                    pages.append(text)
        except pypdfium2.PdfiumError:
            raise DocumentError("Не удалось открыть PDF. Проверьте, что файл читается и доступен без пароля.") from None
        parts = tuple(pages)
    else:
        raise DocumentError("Для проверки текста поддерживаются TXT, MD, DOCX и PDF с текстовым слоем.")
    if sum(map(len, parts)) > MAX_TEXT:
        raise DocumentError("В документе слишком много текста для проверки.")
    if not any(WORDS.search(text) for text in parts):
        raise DocumentError("В файле нет доступного текста. Для PDF-скана нужен текстовый слой.")
    if stamp(before) != stamp(path.stat()):
        raise DocumentError("Файл изменился во время чтения. Выберите его заново.")
    return Document(path, size, parts, paginated)
