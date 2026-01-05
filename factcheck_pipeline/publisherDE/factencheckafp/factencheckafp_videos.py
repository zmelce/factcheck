
from __future__ import annotations

import re
import time
from html import unescape
from typing import List, Dict
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl, parse_qs

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

YT_EMBED = re.compile(r"(?:https?:)?//(?:www\.)?(?:youtube\.com|youtube\-nocookie\.com)/embed/([A-Za-z0-9_\-]{6,})", re.I)
YT_WATCH = re.compile(r"(?:https?:)?//(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_\-]{6,})", re.I)

TW_EMBED_HOST = "platform.twitter.com"
TW_EMBED_PATH = "/embed/Tweet.html"

CONSENT_TEXTS = [
    "Accept all","Agree","I agree","Continue without consent","Reject all",
    "Alle akzeptieren","Alles akzeptieren","Akzeptieren","Zustimmen","Alle ablehnen",
    "Continuer sans consentir","Tout accepter","Tout refuser",
]

def add_autoplay(u: str, autoplay=True) -> str:
    if not autoplay:
        return u
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs["autoplay"] = "1"
    return urlunsplit((sp.scheme or "https", sp.netloc, sp.path, urlencode(qs), sp.fragment))

def canonicalize_youtube(urls, autoplay=True):
    seen = set()
    out = []
    for u in urls:
        if not u:
            continue
        m = YT_EMBED.search(u) or YT_WATCH.search(u)
        if not m:
            continue
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        out.append(add_autoplay(f"https://www.youtube.com/embed/{vid}", autoplay=autoplay))
    return out

def extract_tweet_id_from_url(u: str) -> str | None:
    try:
        sp = urlsplit(u)
        if sp.netloc.endswith(TW_EMBED_HOST) and sp.path.endswith(TW_EMBED_PATH):
            q = parse_qs(sp.query)
            tid = (q.get("id") or q.get("tweet_id") or [None])[0]
            return tid
    except:
        pass
    return None

def canonicalize_twitter(urls, data_ids=None):
    seen = set()
    out = []
    data_ids = data_ids or []
    for tid in data_ids:
        if tid and tid not in seen:
            seen.add(tid)
            out.append(f"https://twitter.com/i/web/status/{tid}")
    for u in urls:
        tid = extract_tweet_id_from_url(u)
        if tid and tid not in seen:
            seen.add(tid)
            out.append(f"https://twitter.com/i/web/status/{tid}")
    return out

def make_driver(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=en-US,en;q=0.9")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    return webdriver.Chrome(options=opts)

def try_accept_consents(driver, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        for t in CONSENT_TEXTS:
            xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
            for el in driver.find_elements(By.XPATH, xp):
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    time.sleep(0.05)
                    el.click()
                    return
                except:
                    pass
        for fr in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.frame(fr)
                for t in CONSENT_TEXTS:
                    xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
                    for el in driver.find_elements(By.XPATH, xp):
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                            time.sleep(0.05)
                            el.click()
                            driver.switch_to.default_content()
                            return
                        except:
                            pass
                driver.switch_to.default_content()
            except:
                try: driver.switch_to.default_content()
                except: pass
        time.sleep(0.2)

def extract_video_iframes(driver, scope_css: str | None = None, autoplay_youtube=True) -> Dict[str, List[str]]:
    root = driver if not scope_css else driver.find_element(By.CSS_SELECTOR, scope_css)

    raw_urls = []
    tweet_data_ids = []

    for fr in root.find_elements(By.CSS_SELECTOR, "iframe"):
        src = fr.get_attribute("src") or ""
        if src:
            raw_urls.append(src)

        tid = fr.get_attribute("data-tweet-id") or ""
        if tid:
            tweet_data_ids.append(tid)

        srcdoc = fr.get_attribute("srcdoc") or ""
        if srcdoc:
            html = unescape(srcdoc)
            raw_urls += re.findall(r'(?:src|href)=["\']([^"\']+)["\']', html, flags=re.I)

    normalized = []
    for u in raw_urls:
        if u.startswith("//"):
            u = "https:" + u
        normalized.append(u)

    youtube = canonicalize_youtube(normalized, autoplay=autoplay_youtube)
    twitter = canonicalize_twitter(normalized, data_ids=tweet_data_ids)

    return {"youtube": youtube, "twitter": twitter}

def handle(review_url: str, headless: bool = True) -> List[str]:
    driver = make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        try_accept_consents(driver, timeout=8)

        links = extract_video_iframes(driver, scope_css=None, autoplay_youtube=True)
        out: List[str] = []
        out.extend(links.get("youtube", []))
        out.extend(links.get("twitter", []))
        return out
    finally:
        driver.quit()

