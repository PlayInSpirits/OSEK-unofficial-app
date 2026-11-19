"""Find exact matches in public search results for a sample of a document."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
import ipaddress
from urllib.parse import quote, urlsplit
import webbrowser

import requests
from bs4 import BeautifulSoup

from .document_check import DocumentError, WORDS


GROUPS = {"general": ("plumb", "wiki"), "academic": ("doaj", "wiki")}
USER_AGENT = "OSEK-Remastered (https://github.com/PlayInSpirits/OSEK-unofficial-app)"
MAX_SAMPLES = 6
SAMPLE_WORDS = 8


@dataclass(frozen=True)
class Sample:
    part: int
    text: str
    words: tuple[str, ...]


def samples_for(document):
    candidates = []
    for index, part in enumerate(document.parts, 1):
        # Short contiguous phrases, spread across the document; skip numeric and link segments.
        for line in part.splitlines():
            if any(marker in line for marker in ('@', '://', '\\')):
                continue
            words = [(m.group(), m.start(), m.end()) for m in WORDS.finditer(line)]
            for pos in range(0, len(words) - SAMPLE_WORDS + 1, SAMPLE_WORDS):
                window = words[pos:pos + SAMPLE_WORDS]
                terms = tuple(word.casefold() for word, _, _ in window)
                if not all(term.isalpha() or '-' in term and term.replace('-', '').isalpha() for term in terms):
                    continue
                display = line[window[0][1]:window[-1][2]]
                if len(display) <= 180:
                    candidates.append(Sample(index, display, terms))
        # PDF and narrow documents may wrap lines before a complete phrase.
    if not candidates:
        for index, part in enumerate(document.parts, 1):
            lines = [line for line in part.splitlines() if not any(ch in line for ch in ('@', '://', '\\'))]
            terms = tuple(word.casefold() for word in WORDS.findall(' '.join(lines)))
            for pos in range(0, len(terms) - SAMPLE_WORDS + 1, SAMPLE_WORDS):
                window = terms[pos:pos + SAMPLE_WORDS]
                if all(word.isalpha() for word in window) and len(' '.join(window)) <= 180:
                    candidates.append(Sample(index, ' '.join(window), window))
    unique = list({sample.words: sample for sample in reversed(candidates)}.values())[::-1]
    if len(unique) <= MAX_SAMPLES:
        return unique
    return [unique[round(i * (len(unique) - 1) / (MAX_SAMPLES - 1))] for i in range(MAX_SAMPLES)]


def public_url(value):
    if not isinstance(value, str):
        return None
    if any(ord(ch) < 33 or ch.isspace() for ch in value) or '\\' in value:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme not in {'http', 'https'} or parsed.username is not None or parsed.password is not None
                or '.' not in host or host.endswith(('.localhost', '.local', '.internal')) or parsed.port is not None):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if not host.rsplit('.', 1)[-1].isalpha():
                return None
    except ValueError:
        return None
    return value


def open_check_link(url):
    url = public_url(url)
    return bool(url and webbrowser.open(url))


def get_json(session, url, **params):
    response = session.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=(4, 6), allow_redirects=False)
    if response.status_code != 200:
        raise requests.HTTPError(response=response)
    body = response.json()
    if not isinstance(body, dict) or 'error' in body:
        raise ValueError('Unexpected response from search index')
    return body


def search_plumb(session, phrase):
    body = get_json(session, 'https://plumbsearch.org/search', q=phrase, format='json')
    return [(row.get('url'), row.get('title'), row.get('content')) for row in body['results'][:5]]


def search_wiki(session, phrase):
    language = 'ru' if any('а' <= letter <= 'я' or letter == 'ё' for letter in phrase) else 'en'
    body = get_json(session, f'https://{language}.wikipedia.org/w/api.php', action='query', generator='search',
                    gsrsearch=f'"{phrase}"', gsrnamespace=0, gsrlimit=1, prop='extracts|info',
                    explaintext=1, exlimit=1, inprop='url', format='json', formatversion=2)
    return [(row.get('fullurl'), row.get('title'), row.get('extract'))
            for row in body.get('query', {}).get('pages', [])]


def search_doaj(session, phrase):
    expression = quote(f'bibjson.abstract:"{phrase}"', safe='')
    body = get_json(session, f'https://doaj.org/api/v4/search/articles/{expression}', pageSize=5)
    return [(f"https://doaj.org/article/{row['id']}", row['bibjson'].get('title'), row['bibjson'].get('abstract'))
            for row in body['results'] if 'id' in row and 'bibjson' in row]


SEARCHERS = {'plumb': search_plumb, 'wiki': search_wiki, 'doaj': search_doaj}


def check_online(document, group, progress, cancelled=lambda: False):
    samples = samples_for(document)
    if not samples:
        raise DocumentError('Для поиска нужна хотя бы одна фраза из восьми слов.')
    providers = GROUPS[group]
    total = len(samples) * len(providers)
    completed = 0
    lock = Lock()
    progress(0, total)

    def mark(count):
        nonlocal completed
        with lock:
            completed += count
            progress(completed, total)

    def run(provider):
        matches = []
        checked = 0
        error = ''
        with requests.Session() as session:
            for sample in samples:
                if cancelled():
                    break
                try:
                    candidates = SEARCHERS[provider](session, ' '.join(sample.words))
                except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                    status = getattr(getattr(exc, 'response', None), 'status_code', None)
                    error = 'Достигнут лимит запросов' if status == 429 else 'Источник не ответил или вернул ошибку'
                    break
                checked += 1
                sources = []
                phrase = ' ' + ' '.join(sample.words) + ' '
                for url, title, content in candidates:
                    url = public_url(url)
                    if not url or not isinstance(content, str) or not content:
                        continue
                    plain = BeautifulSoup(content, 'html.parser').get_text(' ') if '<' in content else content
                    if phrase in ' ' + ' '.join(WORDS.findall(plain.casefold())) + ' ' and not any(s['url'] == url for s in sources):
                        sources.append({'url': url, 'title': (title if isinstance(title, str) and title else url)[:200]})
                if sources:
                    matches.append({'text': sample.text, 'page': sample.part if document.paginated else None, 'sources': sources})
                mark(1)
        mark(len(samples) - checked)
        return {'id': provider, 'checked': checked, 'sampled': len(samples), 'matches': matches, 'error': error}

    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        return list(pool.map(run, providers))
