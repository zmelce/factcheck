from __future__ import annotations

import json
import re
import time
from html import unescape
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from readability import Document

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8,de;q=0.7",
    "Connection": "keep-alive",
}

STOP_PHRASES = [
    "Copyright © AFP",
    "copyright © afp",
]

CONSENT_TEXTS = [
    "Accept all",
    "Accept",
    "I accept",
    "Continue",
    "Alle zulassen",
    "Alle akzeptieren",
    "Akzeptieren",
    "Zustimmen",
    "Alle ablehnen",
    "Ablehnen",
]


_MENTIONS_COPYRIGHT_RE = re.compile(
    r"<div\b[^>]*class\s*=\s*(['\"][^'\"]*\bmentions-copyright\b[^'\"]*['\"])[^>]*>",
    flags=re.I,
)

def strip_after_mentions_copyright(html: str) -> str:
    if not html:
        return html
    m = _MENTIONS_COPYRIGHT_RE.search(html)
    if not m:
        return html
    return html[: m.start()].strip()


def clean_lines(text: str) -> str:
    text = re.sub(r"\u00a0", " ", text)
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


def strip_html(s: str) -> str:
    return BeautifulSoup(s or "", "lxml").get_text(" ", strip=True)


def looks_like_access_denied(html: str) -> bool:
    if not html:
        return True
    h = html.lower()
    needles = [
        "access denied",
        "zugriff verweigert",
        "accès refusé",
        "request blocked",
        "forbidden",
        "you have been blocked",
        "bot detection",
        "verify you are human",
        "captcha",
        "cloudflare",
        "attention required",
    ]
    return any(n in h for n in needles)


def extract_from_jsonld(soup: BeautifulSoup) -> Tuple[str, str]:
    best_title = ""
    best_body = ""

    scripts = soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)})
    for sc in scripts:
        raw = sc.string or sc.get_text(strip=True) or ""
        raw = raw.strip()
        if not raw:
            continue

        try:
            payload = json.loads(raw)
        except:
            try:
                payload = json.loads(unescape(raw))
            except:
                continue

        stack: List[Any] = [payload]
        while stack:
            obj = stack.pop()

            if isinstance(obj, list):
                stack.extend(obj)
                continue
            if not isinstance(obj, dict):
                continue

            if "@graph" in obj and isinstance(obj["@graph"], list):
                stack.extend(obj["@graph"])

            typ = obj.get("@type")
            types = {t.lower() for t in ([typ] if isinstance(typ, str) else (typ or [])) if isinstance(t, str)}
            if not types:
                continue

            if any(t in types for t in ["newsarticle", "reportagenewsarticle", "article"]):
                t0 = obj.get("headline") or obj.get("name") or ""
                b0 = obj.get("articleBody") or obj.get("text") or ""

                t0 = strip_html(str(t0))
                b0 = strip_html(str(b0))

                if len(b0) > len(best_body):
                    best_title = t0 or best_title
                    best_body = b0 or best_body

    return best_title.strip(), clean_lines(best_body)


def extract_from_dom(soup: BeautifulSoup) -> Tuple[str, str]:
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)

    container = soup.find("article")
    if not container:
        selectors = [
            "[itemprop='articleBody']",
            "div.article-body",
            "div.article__body",
            "div.c-article__body",
            "div.story-body",
            "div.content__body",
            "div.wysiwyg",
            "main",
        ]
        for sel in selectors:
            container = soup.select_one(sel)
            if container:
                break

    if not container:
        return title.strip(), ""

    for tag in container.find_all(["script", "style", "noscript", "aside", "footer", "form"]):
        tag.decompose()

    parts: List[str] = []
    for el in container.find_all(["h2", "h3", "p", "blockquote", "li"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = cut_at_stop_phrases(body)
    return title.strip(), body


def extract_with_readability(html: str) -> Tuple[str, str]:
    doc = Document(html)
    title = (doc.short_title() or "").strip()

    main_html = doc.summary(html_partial=True)
    soup = BeautifulSoup(main_html, "lxml")

    parts: List[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "p", "blockquote", "li"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            parts.append(txt)

    body = clean_lines("\n".join(parts))
    body = cut_at_stop_phrases(body)
    return title, body


def fetch_html_requests(url: str, timeout: int = 30) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    if r.status_code == 403:
        raise RuntimeError(f"HTTP 403 (blocked) for {url}")
    r.raise_for_status()
    return r.text


def click_consent_if_present_playwright(page, timeout: int = 6) -> None:
    end = time.time() + timeout
    click_sel = "button, input[type='button'], input[type='submit'], a[role='button']"

    while time.time() < end:
        for frame in page.frames:
            for t in CONSENT_TEXTS:
                try:
                    loc = frame.locator(click_sel).filter(has_text=t)
                    if loc.count() > 0:
                        el = loc.first
                        try:
                            el.scroll_into_view_if_needed(timeout=500)
                        except:
                            pass
                        try:
                            el.click(timeout=900)
                        except:
                            try:
                                el.click(timeout=900, force=True)
                            except:
                                continue

                        page.wait_for_timeout(200)
                        return
                except:
                    continue

        page.wait_for_timeout(250)


def fetch_html_playwright(
    url: str,
    headless: bool = True,
    timeout: int = 30,
    *,
    warmup_url: str | None = None,
    channel: str | None = "chrome",
) -> str:
    with sync_playwright() as p:
        launch_kwargs = {
            "headless": headless,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        }
        if channel:
            launch_kwargs["channel"] = channel

        browser = p.chromium.launch(**launch_kwargs)

        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="fr-FR",
            viewport={"width": 1440, "height": 1100},
            extra_http_headers={
                "Accept": HEADERS["Accept"],
                "Accept-Language": HEADERS["Accept-Language"],
            },
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        page = context.new_page()
        try:
            if warmup_url:
                try:
                    page.goto(warmup_url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    page.wait_for_selector("body", timeout=10_000)
                    click_consent_if_present_playwright(page, timeout=6)
                except:
                    pass

            resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

            if resp is not None and resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status} for {url}")

            try:
                page.wait_for_selector("body", timeout=timeout * 1000)
            except PlaywrightTimeoutError:
                pass

            click_consent_if_present_playwright(page, timeout=8)

            try:
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(150)
                page.evaluate("window.scrollTo(0, 0);")
            except:
                pass

            html = page.content() or ""
            if looks_like_access_denied(html):
                raise RuntimeError(f"Access Denied -> {url}")

            return html
        finally:
            try:
                context.close()
            finally:
                browser.close()


def is_afp_faktencheck(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("faktencheck.afp.com") or host.endswith("afp.com")


def fetch_and_extract(
    url: str,
    timeout: int = 30,
    playwright_fallback: bool = True,
    headless: bool = True,
    *,
    playwright_primary_for_afp: bool = True,
) -> Dict[str, str]:
    html = ""
    used_playwright = False

    is_afp = is_afp_faktencheck(url)

    if is_afp and playwright_primary_for_afp:
        html = fetch_html_playwright(
            url,
            headless=headless,
            timeout=timeout,
            warmup_url="https://faktencheck.afp.com/",
        )
        used_playwright = True
    else:
        try:
            html = fetch_html_requests(url, timeout=timeout)
            if looks_like_access_denied(html):
                raise RuntimeError(f"Access Denied -> {url}")
        except Exception as e:
            if playwright_fallback and ("403" in str(e) or "Access Denied" in str(e)):
                warmup = "https://faktencheck.afp.com/" if is_afp else None
                html = fetch_html_playwright(url, headless=headless, timeout=timeout, warmup_url=warmup)
                used_playwright = True
            else:
                raise

    html = strip_after_mentions_copyright(html)

    soup = BeautifulSoup(html, "lxml")

    t1, b1 = extract_from_jsonld(soup)
    if len(b1) >= 400:
        return {"url": url, "title": t1, "content": b1}

    t2, b2 = extract_from_dom(soup)
    if len(b2) >= len(b1):
        t_best, b_best = (t2 or t1), b2
    else:
        t_best, b_best = (t1 or t2), b1

    if len(b_best) < 300:
        t3, b3 = extract_with_readability(html)
        if len(b3) > len(b_best):
            t_best, b_best = (t3 or t_best), b3

    if playwright_fallback and not used_playwright and len(b_best) < 250:
        try:
            warmup = "https://faktencheck.afp.com/" if is_afp else None
            html2 = fetch_html_playwright(url, headless=headless, timeout=timeout, warmup_url=warmup)

            html2 = strip_after_mentions_copyright(html2)

            soup2 = BeautifulSoup(html2, "lxml")

            tt1, bb1 = extract_from_jsonld(soup2)
            tt2, bb2 = extract_from_dom(soup2)

            cand_title, cand_body = (tt2 or tt1), (bb2 if len(bb2) >= len(bb1) else bb1)
            if len(cand_body) < 300:
                tt3, bb3 = extract_with_readability(html2)
                if len(bb3) > len(cand_body):
                    cand_title, cand_body = (tt3 or cand_title), bb3

            if len(cand_body) > len(b_best):
                t_best, b_best = cand_title, cand_body
        except:
            pass

    return {"url": url, "title": (t_best or "").strip(), "content": (b_best or "").strip()}
