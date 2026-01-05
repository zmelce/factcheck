from __future__ import annotations

import re
from typing import Tuple

import requests
from bs4 import BeautifulSoup, Tag

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

REMOVE_SELECTORS = [
    ".sfsiaftrpstwpr",
    ".sfsi_responsive_icons",
    "#comment-wrap",
    "#respond",
    ".comment-respond",
    "script",
    "style",
    "noscript",
]


def clean_lines(text: str) -> str:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def extract_fasocheck(html: str) -> Tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")

    title = ""
    h1 = soup.find("h1", class_="entry-title")
    if not h1:
        h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    container = soup.find("div", class_="entry-content")
    if not container:
        container = soup.find("article")
    if not container:
        container = soup.find(id="left-area")

    if not container:
        return title, ""

    for sel in REMOVE_SELECTORS:
        for tag in container.select(sel):
            tag.decompose()

    for div in container.find_all("div"):
        classes = " ".join(div.get("class", []))
        if "sfsi" in classes or "share" in classes.lower():
            for sib in list(div.find_next_siblings()):
                if isinstance(sib, Tag):
                    sib.decompose()
            div.decompose()
            break

    parts = []
    for el in container.find_all(["h1", "h2", "h3", "h4", "p", "blockquote", "li"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    return title, body


def fetch_and_extract(url: str, timeout: int = 30) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    title, body = extract_fasocheck(html)

    return {"url": url, "title": title, "content": body}