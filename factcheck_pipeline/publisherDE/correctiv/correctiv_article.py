from __future__ import annotations

import re
from typing import Tuple

import requests
from bs4 import BeautifulSoup
from readability import Document

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

STOP_PHRASES = [
    "CORRECTIV im Postfach",
    "Ihre Unterstützung zählt",
    "Jetzt unterstützen",
    "Spendenkonto",
    "Kontakt",
    "Zentrale Essen",
    "Redaktion Berlin",
    "Buchladen Essen",
    "Jugendredaktion",
    "Hinweise geben",
    "Datenschutz",
    "Online-Shop",
    "Newsletter",
]

INLINE_DROP_LINES = [
    "Mehr von CORRECTIV",
]


def clean_lines(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def cut_at_stop_phrases(text: str) -> str:
    """
    Line-based cutoff (safe): only stop when a footer marker appears as its own line
    or at the start of a line. This prevents cutting mid-sentence like:
      "... weisen kein Impressum auf ..."
    """
    lines = (text or "").splitlines()
    out = []
    for ln in lines:
        l = ln.strip()
        l_low = l.lower()
        if any(l_low == p.lower() or l_low.startswith(p.lower()) for p in STOP_PHRASES):
            break
        out.append(ln)
    return "\n".join(out).strip()


def remove_inline_widgets(text: str) -> str:
    lines = (text or "").splitlines()
    out = []
    skip_next = 0

    for ln in lines:
        if skip_next > 0:
            skip_next -= 1
            continue

        l = ln.strip()
        l_low = l.lower()

        if any(l_low == x.lower() for x in INLINE_DROP_LINES):
            skip_next = 1
            continue

        if l_low in {"weiterlesen", "mehr erfahren", "jetzt spenden", "gerade nicht"}:
            continue

        out.append(ln)

    return "\n".join(out).strip()


def _extract_from_detail_content(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    container = soup.select_one("div.detail__content")
    if not container:
        return title, ""

    for cut in container.select("div.corre-abbinder-events"):
        cut.decompose()

    for sel in [
        "div.related__container",
        "div.corre-nach-dem-3-absatz-recherche",
        "div.corre-target",
    ]:
        for t in container.select(sel):
            t.decompose()

    for tag in container.find_all(["script", "style", "noscript", "form", "aside", "footer"]):
        tag.decompose()

    parts = []
    for el in container.find_all(["h2", "h3", "p", "blockquote", "li", "figcaption"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = remove_inline_widgets(body)
    body = cut_at_stop_phrases(body)

    return title, body


def extract_with_readability(html: str) -> Tuple[str, str]:
    doc = Document(html)
    title = (doc.short_title() or "").strip()

    main_html = doc.summary(html_partial=True)
    soup = BeautifulSoup(main_html, "lxml")

    parts = []
    for el in soup.find_all(["h1", "h2", "h3", "p", "blockquote", "li", "figcaption"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = remove_inline_widgets(body)
    body = cut_at_stop_phrases(body)
    return title, body


def extract_with_fallback(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    container = soup.find("article")
    if not container:
        selectors = [
            "main article",
            "div.entry-content",
            "div.post-content",
            "div.wp-block-post-content",
            "[itemprop='articleBody']",
            "main",
        ]
        for sel in selectors:
            container = soup.select_one(sel)
            if container:
                break

    if not container:
        text = clean_lines(soup.get_text("\n", strip=True))
        text = remove_inline_widgets(text)
        return title, cut_at_stop_phrases(text)

    for tag in container.find_all(["script", "style", "noscript", "aside", "footer", "form"]):
        tag.decompose()

    parts = []
    for el in container.find_all(["h2", "h3", "p", "blockquote", "li", "figcaption"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = remove_inline_widgets(body)
    body = cut_at_stop_phrases(body)
    return title, body


def fetch_and_extract(url: str, timeout: int = 30) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    title, body = _extract_from_detail_content(html)
    if len(body) >= 400:
        return {"url": url, "title": title, "content": body}

    t2, b2 = extract_with_readability(html)
    if len(b2) > len(body):
        title, body = (t2 or title), b2

    if len(body) < 250:
        t3, b3 = extract_with_fallback(html)
        if len(b3) > len(body):
            title, body = (t3 or title), b3

    return {"url": url, "title": title, "content": body}
