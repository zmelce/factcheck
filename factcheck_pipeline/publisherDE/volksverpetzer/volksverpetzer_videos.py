
from __future__ import annotations

import re
import time
from html import unescape
from typing import List
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import tempfile
from selenium.webdriver.chrome.service import Service

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

CONSENT_TEXTS = [
    "Cookies akzeptieren","Alle akzeptieren","Alles akzeptieren","Zustimmen","Akzeptieren","Ich stimme zu",
    "Ohne Zustimmung fortfahren","Weiter ohne Einwilligung","Ablehnen","Alle ablehnen",
    "Accept all","Agree","I agree","Continue without consent","Reject all",
    "Continuer sans consentir","Tout accepter","Tout refuser",
]

Y_EMBED = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})",
    re.I,
)
FB_PLUGIN_RE = re.compile(
    r"https?://(?:www\.)?facebook\.com/plugins/video\.php\?[^\"'>]+",
    re.I,
)



from selenium.webdriver.chrome.service import Service

CHROMEDRIVER_PATH = "/Users/melce/bin/chromedriver"

def _make_driver(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")

    opts.add_argument("--window-size=1440,1100")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=de-DE,de;q=0.9,en;q=0.8")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={UA}")
    opts.add_argument("--remote-debugging-port=0")

    service = Service(
        executable_path=CHROMEDRIVER_PATH,
        port=0,
        log_output="chromedriver_videos.log"
    )
    return webdriver.Chrome(service=service, options=opts)

def _click_first_button_with_text(driver, texts) -> bool:
    for t in texts:
        xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
        for el in driver.find_elements(By.XPATH, xp):
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.07)
                el.click()
                return True
            except Exception:
                continue
    return False

def _accept_consents(driver, timeout: int = 18) -> None:
    end = time.time() + timeout
    tried_iframes = False
    while time.time() < end:
        if _click_first_button_with_text(driver, CONSENT_TEXTS):
            return
        if not tried_iframes:
            for fr in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    driver.switch_to.frame(fr)
                    if _click_first_button_with_text(driver, CONSENT_TEXTS):
                        driver.switch_to.default_content()
                        return
                    driver.switch_to.default_content()
                except Exception:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
            tried_iframes = True
        time.sleep(0.25)

def _slow_scroll(driver, steps: int = 10, dy: int = 1400, pause: float = 0.18) -> None:
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
        time.sleep(pause)

def _add_query(u: str, extra: dict) -> str:
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs.update(extra or {})
    return urlunsplit((sp.scheme or "https", sp.netloc, sp.path, urlencode(qs), sp.fragment))

def _youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"

def _extract_youtube_facade_and_iframes(driver) -> List[str]:
    ids = set()

    facades = driver.find_elements(
        By.CSS_SELECTOR,
        "div[data-facadesrc*='youtube.com/embed'], div[data-facadesrc*='youtube-nocookie.com/embed']"
    )
    for f in facades:
        src = (f.get_attribute("data-facadesrc") or "").strip()
        if not src:
            continue
        src = unescape(src)
        m = Y_EMBED.search(src)
        if m:
            ids.add(m.group(1))

    iframes = driver.find_elements(
        By.CSS_SELECTOR,
        "iframe[src*='youtube.com/embed'], iframe[src*='youtube-nocookie.com/embed']"
    )
    for fr in iframes:
        src = (fr.get_attribute("src") or "").strip()
        if src:
            src = unescape(src)
            m = Y_EMBED.search(src)
            if m:
                ids.add(m.group(1))

        srcdoc = (fr.get_attribute("srcdoc") or "").strip()
        if srcdoc:
            for mm in Y_EMBED.finditer(srcdoc):
                ids.add(mm.group(1))

    return sorted(_youtube_watch_url(i) for i in ids)

def _extract_facebook_plugin_only(driver) -> List[str]:
    urls = set()

    for fr in driver.find_elements(By.CSS_SELECTOR, "iframe[src*='facebook.com/plugins/video.php']"):
        src = (fr.get_attribute("src") or "").strip()
        if src:
            urls.add(src)

    for el in driver.find_elements(By.CSS_SELECTOR, "[data-src-cmplz*='facebook.com/plugins/video.php']"):
        ds = (el.get_attribute("data-src-cmplz") or "").strip()
        if ds:
            urls.add(ds)

    html = driver.page_source or ""
    for m in FB_PLUGIN_RE.finditer(html):
        urls.add(unescape(m.group(0)))

    return sorted(urls)

def handle(review_url: str, headless: bool = True) -> List[str]:
    driver = _make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        _accept_consents(driver, timeout=18)

        _slow_scroll(driver, steps=10, dy=1200, pause=0.18)
        driver.execute_script("window.scrollTo(0,0);")
        time.sleep(0.3)

        yt = _extract_youtube_facade_and_iframes(driver)   # now returns watch URLs
        fb = _extract_facebook_plugin_only(driver)         # unchanged

        return (yt or []) + (fb or [])
    finally:
        driver.quit()
