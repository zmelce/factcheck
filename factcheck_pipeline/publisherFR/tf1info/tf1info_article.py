from __future__ import annotations

import re
import unicodedata
from typing import Tuple

import requests
from bs4 import BeautifulSoup
from readability import Document

STOP_PHRASES = [
    "Passend dazu",
    "Du liest Artikel zu Ende",
    "MÖCHTEST DU MEHR",
    "Unterstützen",
    "Vous souhaitez nous poser des questions ou nous soumettre une information qui ne vous paraît pas fiable ? "
    "N'hésitez pas à nous écrire à l'adresse lesverificateurs@tf1.fr. "
    "Retrouvez-nous également sur X : notre équipe y est présente derrière le compte @verif_TF1LCI.",
    ""
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,de-DE,de;q=0.8,en;q=0.7",
}


def _normalize_for_match(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u00a0", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_lines(text: str) -> str:
    text = text.replace("\u00a0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def cut_at_stop_phrases(text: str) -> str:
    """
    Cut article body at the earliest occurrence of any STOP_PHRASE.
    Matching is accent-insensitive and whitespace-normalized.
    We compute the cut index on a normalized copy, then map back to original
    by cutting at the corresponding prefix length (approx via incremental scan).
    """
    if not text:
        return text

    norm_text = _normalize_for_match(text)
    cut_norm_idx: int | None = None

    for phrase in STOP_PHRASES:
        p = _normalize_for_match(phrase)
        if not p:
            continue
        i = norm_text.find(p)
        if i != -1:
            cut_norm_idx = i if cut_norm_idx is None else min(cut_norm_idx, i)

    if cut_norm_idx is None:
        return text.strip()

    acc = []
    orig_cut = 0
    for j, ch in enumerate(text):
        chunk = _normalize_for_match(ch)
        if chunk:
            acc.append(chunk)
        cur = " ".join("".join(acc).split())  # collapse spaces similarly
    norm_running = ""
    orig_cut = len(text)
    for j, ch in enumerate(text):
        nch = _normalize_for_match(ch)
        if nch:
            norm_running += nch
        else:
            norm_running += " "

        norm_running = re.sub(r"\s+", " ", norm_running)

        if len(norm_running) >= cut_norm_idx:
            orig_cut = j
            break

    return text[:orig_cut].strip()


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
        return title, cut_at_stop_phrases(text)

    for tag in container.find_all(["script", "style", "noscript"]):
        tag.decompose()

    parts = []
    for el in container.find_all(["h2", "h3", "p", "blockquote", "li"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = cut_at_stop_phrases(body)
    return title, body


def fetch_and_extract(url: str, timeout: int = 30) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    title, body = extract_with_readability(html)

    if len(body) < 200:
        t2, b2 = extract_with_fallback(html)
        if len(b2) > len(body):
            title, body = (t2 or title), b2

    return {"url": url, "title": title, "content": body}
