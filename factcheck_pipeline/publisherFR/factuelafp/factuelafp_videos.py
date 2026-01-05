
import re
import time
from html import unescape
from typing import List, Tuple
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

__all__ = ["extract_links", "handle"]


def make_driver(headless: bool = True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=fr-FR,fr;q=0.9,en;q=0.8")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)

def slow_scroll(driver, steps=10, dy=1200, pause=0.18):
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
        time.sleep(pause)


Y_EMBED = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})", re.I)
Y_WATCH = re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{6,})", re.I)

DM_EMBED = re.compile(r"https?://(?:www\.)?dailymotion\.com/embed/video/([A-Za-z0-9]+)", re.I)
DM_WATCH = re.compile(r"https?://(?:www\.)?dailymotion\.com/video/([A-Za-z0-9]+)", re.I)

ULT_IFRAME = re.compile(r"(?:https?:)?//(?:www\.)?ultimedia\.com/deliver/generic/iframe/[^\"'\s]+", re.I)
RAW_VIDEO = re.compile(r"https?://[^\"'>]+\.(?:mp4|m3u8)(?:\?[^\"'>]*)?", re.I)

def absolutize(u: str, scheme="https"):
    return f"{scheme}:{u}" if u.startswith("//") else u

def add_autoplay(u: str) -> str:
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs["autoplay"] = "1"
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(qs), sp.fragment))

def extract_attr_urls(html: str, attrs=("src","href")):
    out = []
    for a in attrs:
        out += re.findall(fr'{a}="([^"]+)"', html, flags=re.I)
        out += re.findall(fr"{a}='([^']+)'", html, flags=re.I)
    return out

def extract_from_srcdoc(srcdoc: str):
    if not srcdoc:
        return []
    return extract_attr_urls(unescape(srcdoc))

def dedupe_keep_shortest(urls):
    urls = sorted(set(urls), key=lambda x: (len(x), x))
    kept = []
    for u in urls:
        if any(u.startswith(k) for k in kept):
            continue
        kept = [k for k in kept if not k.startswith(u)]
        kept.append(u)
    return kept

def canonicalize_youtube(urls):
    ids, out = set(), []
    for u in urls:
        u = absolutize(u)
        m = Y_EMBED.search(u) or Y_WATCH.search(u)
        if not m:
            continue
        vid = m.group(1)
        if vid in ids:
            continue
        ids.add(vid)
        out.append(add_autoplay(f"https://www.youtube.com/embed/{vid}"))
    return out

def canonicalize_dailymotion(urls):
    ids, out = set(), []
    for u in urls:
        u = absolutize(u)
        m = DM_EMBED.search(u) or DM_WATCH.search(u)
        if not m:
            continue
        vid = m.group(1)
        if vid in ids:
            continue
        ids.add(vid)
        out.append(f"https://www.dailymotion.com/embed/video/{vid}")
    return out

def canonicalize_ultimedia(urls):
    cleaned = []
    for u in urls:
        u = absolutize(u).strip().strip('"').strip("'")
        cleaned.append(u)
    return dedupe_keep_shortest(cleaned)


def scrape_in_factuel_block(driver) -> tuple[list[str], list[str], list[str], list[str]]:
    try:
        block = driver.find_element(
            By.CSS_SELECTOR,
            "div#block-factuel-content.block.block-system.block-system-main-block"
        )
    except Exception:
        return [], [], [], []

    items = block.find_elements(By.CSS_SELECTOR, "div.g2item.g2web, div.g2web")

    urls = []
    for it in items:
        outer = it.get_attribute("outerHTML") or ""
        urls += extract_attr_urls(outer)

        for fr in it.find_elements(By.CSS_SELECTOR, "iframe"):
            s = (fr.get_attribute("src") or "").strip()
            if s:
                urls.append(s)
            sd = fr.get_attribute("srcdoc") or ""
            if sd:
                urls += extract_from_srcdoc(sd)

    urls = [absolutize(u) for u in urls]

    yt_candidates  = [u for u in urls if "youtube.com" in u or "youtube-nocookie.com" in u]
    dm_candidates  = [u for u in urls if "dailymotion.com" in u]
    ult_candidates = [u for u in urls if "ultimedia.com/deliver/generic/iframe" in u or ULT_IFRAME.search(u)]
    raw_candidates = [u for u in urls if RAW_VIDEO.search(u)]

    yt  = canonicalize_youtube(yt_candidates)
    dm  = canonicalize_dailymotion(dm_candidates)
    ult = canonicalize_ultimedia(ult_candidates)
    raw = dedupe_keep_shortest(raw_candidates)

    return yt, dm, ult, raw


def extract_links(review_url: str, headless: bool = True) -> List[str]:
    driver = make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        slow_scroll(driver, steps=12)

        yt, dm, ult, raw = scrape_in_factuel_block(driver)

        flat = yt + dm + ult + raw
        return sorted(set(flat))
    except Exception:
        return []
    finally:
        driver.quit()

def handle(review_url: str) -> List[str]:
    return extract_links(review_url, headless=True)
