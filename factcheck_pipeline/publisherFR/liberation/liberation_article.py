from __future__ import annotations

import re
from typing import Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from readability import Document
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


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
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

PW_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def clean_lines(text: str) -> str:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def cut_at_stop_phrases(text: str) -> str:
    lower = text.lower()
    cut_idx = None
    for phrase in STOP_PHRASES:
        i = lower.find(phrase.lower())
        if i != -1:
            cut_idx = i if cut_idx is None else min(cut_idx, i)
    return text[:cut_idx].strip() if cut_idx is not None else text


def extract_with_readability(html: str) -> Tuple[str, str]:
    doc = Document(html)
    title = (doc.short_title() or "").strip()

    main_html = doc.summary(html_partial=True)
    soup = BeautifulSoup(main_html, "lxml")

    parts: list[str] = []
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

    parts: list[str] = []
    for el in container.find_all(["h2", "h3", "p", "blockquote", "li"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = cut_at_stop_phrases(body)
    return title, body



def add_lazyload_forcers(context) -> None:
    context.add_init_script(
        """
        (() => {
          try {
            const IO = window.IntersectionObserver;
            window.IntersectionObserver = class {
              constructor(cb, opts){ this._cb = cb; this._opts = opts; }
              observe(el){ this._cb([{isIntersecting:true, intersectionRatio:1, target:el}], this); }
              unobserve(){} disconnect(){} takeRecords(){return[]}
            };
          } catch(e) {}
        })();
        """
    )


def fetch_html_with_playwright(url: str, headless: bool = True, timeout_ms: int = 60000) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-renderer-backgrounding",
            ],
        )
        context = browser.new_context(
            user_agent=PW_UA,
            locale="fr-FR",
            viewport={"width": 1400, "height": 900},
            device_scale_factor=2,
        )
        add_lazyload_forcers(context)
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("load", timeout=15000)
            except PWTimeout:
                pass

            try:
                page.wait_for_function(
                    "() => !!document.querySelector('article, main, body')",
                    timeout=15000,
                )
            except PWTimeout:
                pass

            try:
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(400)
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(400)
                page.evaluate("window.scrollTo(0,0)")
            except Exception:
                pass

            return page.content()

        finally:
            context.close()
            browser.close()



def domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def fetch_and_extract(url: str, timeout: int = 30, headless: bool = True) -> dict:
    html: str | None = None
    used_playwright = False

    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code in (401, 403):
            raise requests.HTTPError(f"HTTP {resp.status_code} for {url}", response=resp)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        html = None

    title = ""
    body = ""
    if html:
        title, body = extract_with_readability(html)
        if len(body) < 200:
            t2, b2 = extract_with_fallback(html)
            if len(b2) > len(body):
                title, body = (t2 or title), b2

    needs_pw = (
        (html is None)
        or (len(body) < 200)
        or (domain(url).endswith("liberation.fr") and len(body) < 800)
    )

    if needs_pw:
        html_pw = fetch_html_with_playwright(url, headless=headless)
        used_playwright = True

        t_pw, b_pw = extract_with_readability(html_pw)
        if len(b_pw) < 200:
            t2, b2 = extract_with_fallback(html_pw)
            if len(b2) > len(b_pw):
                t_pw, b_pw = (t2 or t_pw), b2

        if len(b_pw) > len(body):
            title, body = t_pw, b_pw
            html = html_pw

    return {
        "url": url,
        "title": title,
        "content": body,
        "meta": {
            "used_playwright": used_playwright,
            "content_len": len(body),
        },
    }


def handle(review_url: str) -> dict:
    return fetch_and_extract(review_url, timeout=30, headless=True)
