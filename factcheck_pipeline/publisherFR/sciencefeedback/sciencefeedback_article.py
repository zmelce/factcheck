from __future__ import annotations

import re
from typing import Tuple, List, Optional

import requests
from bs4 import BeautifulSoup
from readability import Document

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

STOP_PHRASES: List[str] = []

FOOTER_SENTINEL_PHRASES = [
    "Science Feedback is a non-partisan, non-profit organization",
    "Get in touch",
    "Published on:",
    "Tags:",
]

FOOTER_OPEN_TAG_RE = re.compile(
    r"<footer\b[^>]*class=(['\"])[^'\"]*\bwp-block-group\b[^'\"]*\bmargin-block-start-xl\b[^'\"]*\1[^>]*>",
    re.IGNORECASE,
)

CONTENT_SELECTORS = [
    "main",
    "article",
    "div.wp-block-post-content",
    "[itemprop='articleBody']",
    "div.entry-content",
    "div.post-content",
]


def clean_lines(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def cut_at_phrases(text: str, phrases: List[str]) -> str:
    if not text:
        return ""
    lower = text.lower()
    cut_idx: Optional[int] = None
    for phrase in phrases:
        if not phrase:
            continue
        i = lower.find(phrase.lower())
        if i != -1:
            cut_idx = i if cut_idx is None else min(cut_idx, i)
    return text[:cut_idx].strip() if cut_idx is not None else text


def truncate_html_before_footer(html: str) -> str:
    if not html:
        return ""

    m = FOOTER_OPEN_TAG_RE.search(html)
    if m:
        return html[: m.start()].strip()

    lower = html.lower()
    cut_idx: Optional[int] = None
    for phrase in FOOTER_SENTINEL_PHRASES:
        p = phrase.lower()
        i = lower.find(p)
        if i != -1:
            cut_idx = i if cut_idx is None else min(cut_idx, i)

    if cut_idx is not None:
        return html[:cut_idx].strip()

    return html


def pick_container(soup: BeautifulSoup):
    for sel in CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el is not None:
            return el
    return soup


def strip_junk(container) -> None:
    for tag in container.find_all(["script", "style", "noscript"]):
        tag.decompose()


def extract_text_from_container(container) -> str:
    wanted = ["h1", "h2", "h3", "h4", "p", "li", "blockquote", "figcaption"]
    parts: List[str] = []
    for el in container.find_all(wanted):
        txt = el.get_text(" ", strip=True)
        txt = clean_lines(txt)
        if txt:
            parts.append(txt)
    return clean_lines("\n".join(parts))


def extract_with_dom(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    container = pick_container(soup)
    strip_junk(container)

    body = extract_text_from_container(container)
    body = cut_at_phrases(body, STOP_PHRASES)

    return title.strip(), body.strip()


def extract_with_readability(html: str) -> Tuple[str, str]:
    doc = Document(html)
    title = (doc.short_title() or "").strip()

    main_html = doc.summary(html_partial=True)
    soup = BeautifulSoup(main_html, "lxml")

    container = pick_container(soup)
    strip_junk(container)

    body = extract_text_from_container(container)
    body = cut_at_phrases(body, STOP_PHRASES)

    return title.strip(), body.strip()


def fetch_and_extract(url: str, timeout: int = 30) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()

    if not resp.encoding or resp.encoding.lower() in {"iso-8859-1", "latin-1"}:
        resp.encoding = resp.apparent_encoding

    html_full = resp.text

    html = truncate_html_before_footer(html_full)

    title, body = extract_with_dom(html)

    if len(body) < 400:
        t2, b2 = extract_with_readability(html)
        if len(b2) > len(body):
            title = t2 or title
            body = b2

    return {"url": resp.url, "title": title, "content": body}
