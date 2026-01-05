from __future__ import annotations

import re
from typing import Tuple

import requests
from bs4 import BeautifulSoup
from readability import Document

STOP_PHRASES = [
    "Passend dazu",
    "Du liest Artikel zu Ende",
    "MÖCHTEST DU MEHR",
    "Unterstützen",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def clean_lines(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def cut_at_stop_phrases(text: str) -> str:
    lower = (text or "").lower()
    cut_idx = None
    for phrase in STOP_PHRASES:
        i = lower.find(phrase.lower())
        if i != -1:
            cut_idx = i if cut_idx is None else min(cut_idx, i)
    return text[:cut_idx].strip() if cut_idx is not None else (text or "").strip()


def extract_ds_chapo(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    el = soup.select_one("div.ds-description span.ds-chapo")
    if el:
        return clean_lines(el.get_text(" ", strip=True))

    el2 = soup.select_one("div.ds-description")
    if el2:
        return clean_lines(el2.get_text(" ", strip=True))

    return ""


def merge_chapo(chapo: str, body: str) -> str:
    chapo = clean_lines(chapo or "")
    body = clean_lines(body or "")

    if not chapo:
        return body

    head = (body[:800] or "").lower()
    if chapo.lower() in head:
        return body

    return clean_lines(f"{chapo}\n\n{body}")


def extract_with_readability(html: str) -> Tuple[str, str]:
    doc = Document(html)
    title = (doc.short_title() or "").strip()

    main_html = doc.summary(html_partial=True)
    soup = BeautifulSoup(main_html, "lxml")

    parts = []
    for el in soup.find_all(["h1", "h2", "h3", "p", "blockquote", "li"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = cut_at_stop_phrases(body)

    chapo = extract_ds_chapo(html)
    body = merge_chapo(chapo, body)

    return title, body


def extract_with_fallback(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    container = soup.find("article")
    if not container:
        for cls in ["entry-content", "post-content", "et_pb_post_content", "post-content-inner"]:
            container = soup.find(class_=re.compile(rf"\b{re.escape(cls)}\b"))
            if container:
                break

    if not container:
        text = clean_lines(soup.get_text("\n", strip=True))
        text = cut_at_stop_phrases(text)
        chapo = extract_ds_chapo(html)
        return title, merge_chapo(chapo, text)

    for tag in container.find_all(["script", "style", "noscript"]):
        tag.decompose()

    parts = []
    for el in container.find_all(["h2", "h3", "p", "blockquote", "li"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = cut_at_stop_phrases(body)

    chapo = extract_ds_chapo(html)
    body = merge_chapo(chapo, body)

    return title, body


def fetch_and_extract(url: str, timeout: int = 30) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()

    if not resp.encoding or resp.encoding.lower() in {"iso-8859-1", "latin-1"}:
        resp.encoding = resp.apparent_encoding

    html = resp.text

    title, body = extract_with_readability(html)

    if len(body) < 200:
        t2, b2 = extract_with_fallback(html)
        if len(b2) > len(body):
            title, body = (t2 or title), b2

    return {"url": resp.url, "title": title, "content": body}
