from __future__ import annotations

import re
import time
from html import unescape
from typing import List
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CONSENT_TEXTS = [
    "Alle akzeptieren", "Alle Cookies akzeptieren",
    "Cookies akzeptieren", "Zustimmen", "Akzeptieren",
    "Ich stimme zu", "Einverstanden",
    "Ohne Zustimmung fortfahren", "Weiter ohne Einwilligung",
    "Ablehnen", "Alle ablehnen",
    "Accept all", "Agree", "Reject all",
]

Y_EMBED = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})",
    re.I,
)
FB_PLUGIN_RE = re.compile(
    r"https?://(?:www\.)?facebook\.com/plugins/video\.php\?[^\"'>]+",
    re.I,
)


def make_driver(headless=True):
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
    return webdriver.Chrome(options=opts)


def click_first_button_with_text(driver, texts) -> bool:
    for t in texts:
        xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
        for el in driver.find_elements(By.XPATH, xp):
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                time.sleep(0.07)
                el.click()
                return True
            except:
                continue
    return False


def accept_consents(driver, timeout: int = 18) -> None:
    end = time.time() + timeout
    tried_iframes = False
    while time.time() < end:
        if click_first_button_with_text(driver, CONSENT_TEXTS):
            return
        if not tried_iframes:
            for fr in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    driver.switch_to.frame(fr)
                    if click_first_button_with_text(driver, CONSENT_TEXTS):
                        driver.switch_to.default_content()
                        return
                    driver.switch_to.default_content()
                except:
                    try:
                        driver.switch_to.default_content()
                    except:
                        pass
            tried_iframes = True
        time.sleep(0.25)


def slow_scroll(driver, steps=10, dy=1400, pause=0.18):
    for _ in range(steps):
        ActionChains(driver).scroll_by_amount(0, dy).perform()
        time.sleep(pause)


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_rtl_native_videos(driver) -> List[str]:
    urls = set()

    RTL_CDN_RE = re.compile(r"https?://[^\"'<>\s]*\.rtl\.de/", re.I)

    def is_rtl_cdn(u):
        return bool(RTL_CDN_RE.match(u))

    mains = driver.find_elements(By.TAG_NAME, "main")
    if not mains:
        return []
    main_el = mains[0]

    for source_el in main_el.find_elements(By.CSS_SELECTOR, "video > source[src]"):
        src = (source_el.get_attribute("src") or "").strip()
        if src and not src.startswith("blob:") and is_rtl_cdn(src):
            urls.add(src)

    for video_el in main_el.find_elements(By.TAG_NAME, "video"):
        src = (video_el.get_attribute("src") or "").strip()
        if src and not src.startswith("blob:") and is_rtl_cdn(src):
            urls.add(src)

    for player in main_el.find_elements(
        By.CSS_SELECTOR,
        "[class*='FoundationPlayer'], [data-testid='test-video-container']"
    ):
        for attr in ["data-src", "data-video-url", "data-stream-url"]:
            val = (player.get_attribute(attr) or "").strip()
            if val and not val.startswith("blob:") and is_rtl_cdn(val):
                urls.add(val)

    try:
        main_html = main_el.get_attribute("innerHTML") or ""
    except:
        main_html = ""

    for m in re.finditer(
        r'https?://vodvmsusoaws-cf\.rtl\.de/[^"\'<>\s]+\.(?:mp4|m3u8)',
        main_html
    ):
        urls.add(unescape(m.group(0)))

    for m in re.finditer(
        r'https?://vod[^"\'<>\s]*\.rtl\.de/[^"\'<>\s]+\.(?:mp4|m3u8)',
        main_html
    ):
        urls.add(unescape(m.group(0)))

    return sorted(urls)


def extract_youtube_embeds(driver) -> List[str]:
    ids = set()

    mains = driver.find_elements(By.TAG_NAME, "main")
    if not mains:
        return []
    main_el = mains[0]

    for f in main_el.find_elements(
        By.CSS_SELECTOR,
        "div[data-facadesrc*='youtube.com/embed'], "
        "div[data-facadesrc*='youtube-nocookie.com/embed']",
    ):
        src = unescape((f.get_attribute("data-facadesrc") or "").strip())
        m = Y_EMBED.search(src)
        if m:
            ids.add(m.group(1))

    for fr in main_el.find_elements(
        By.CSS_SELECTOR,
        "iframe[src*='youtube.com/embed'], "
        "iframe[src*='youtube-nocookie.com/embed']",
    ):
        src = unescape((fr.get_attribute("src") or "").strip())
        m = Y_EMBED.search(src)
        if m:
            ids.add(m.group(1))

        srcdoc = (fr.get_attribute("srcdoc") or "").strip()
        if srcdoc:
            for mm in Y_EMBED.finditer(srcdoc):
                ids.add(mm.group(1))

    return sorted(youtube_watch_url(i) for i in ids)


def extract_facebook_videos(driver) -> List[str]:
    urls = set()

    mains = driver.find_elements(By.TAG_NAME, "main")
    if not mains:
        return []
    main_el = mains[0]

    for fr in main_el.find_elements(
        By.CSS_SELECTOR, "iframe[src*='facebook.com/plugins/video.php']"
    ):
        src = (fr.get_attribute("src") or "").strip()
        if src:
            urls.add(src)

    for el in main_el.find_elements(
        By.CSS_SELECTOR,
        "[data-src-cmplz*='facebook.com/plugins/video.php']",
    ):
        ds = (el.get_attribute("data-src-cmplz") or "").strip()
        if ds:
            urls.add(ds)

    try:
        main_html = main_el.get_attribute("innerHTML") or ""
    except:
        main_html = ""
    for m in FB_PLUGIN_RE.finditer(main_html):
        urls.add(unescape(m.group(0)))

    return sorted(urls)


def handle(review_url: str, headless: bool = True) -> List[str]:
    driver = make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        accept_consents(driver, timeout=18)

        slow_scroll(driver, steps=10, dy=1200, pause=0.18)
        driver.execute_script("window.scrollTo(0,0);")
        time.sleep(0.3)

        rtl = extract_rtl_native_videos(driver)
        yt = extract_youtube_embeds(driver)
        fb = extract_facebook_videos(driver)

        AD_DOMAINS = ("emsservice.de", "adsserver", "adserver", "doubleclick",
                      "googlesyndication", "outbrain", "outbrainimg", "taboola")

        def is_ad(u):
            return any(d in u.lower() for d in AD_DOMAINS)

        VIDEO_ID_RE = re.compile(r"world-([a-f0-9]{20,})-")

        def dedup_rtl_videos(video_list):
            by_id = {}
            other = []
            for u in video_list:
                m = VIDEO_ID_RE.search(u)
                if m:
                    vid = m.group(1)
                    existing = by_id.get(vid)
                    if not existing:
                        by_id[vid] = u
                    elif "progressive" in u and "progressive" not in existing:
                        by_id[vid] = u
                    elif u.endswith(".mp4") and not existing.endswith(".mp4"):
                        by_id[vid] = u
                else:
                    other.append(u)
            return sorted(by_id.values()) + other

        rtl = [u for u in rtl if not is_ad(u)]
        rtl = dedup_rtl_videos(rtl)
        yt = [u for u in yt if not is_ad(u)]
        fb = [u for u in fb if not is_ad(u)]

        return rtl + yt + fb
    finally:
        driver.quit()