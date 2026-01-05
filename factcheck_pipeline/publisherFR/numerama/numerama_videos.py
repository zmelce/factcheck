
import re
import time
from html import unescape
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


Y_EMBED = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})", re.I)
TT_EMBED_ID = re.compile(r"/embed/(?:v2/)?(\d+)", re.I)
TT_CANON = re.compile(r"https?://(?:www\.)?tiktok\.com/@[^/]+/video/\d+", re.I)

def _add_qs(u: str, extra: dict) -> str:
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs.update(extra or {})
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(qs), sp.fragment))

def canon_youtube(u: str) -> Optional[str]:
    m = Y_EMBED.search(u or "")
    if not m:
        return None
    vid = m.group(1)
    return _add_qs(f"https://www.youtube.com/embed/{vid}", {"autoplay": "1"})

def canon_tiktok_from_blockquote(bq) -> Optional[str]:
    cite = (bq.get_attribute("cite") or "").strip()
    if cite and TT_CANON.search(cite):
        return TT_CANON.search(cite).group(0)
    vid = (bq.get_attribute("data-video-id") or "").strip()
    user = (bq.get_attribute("data-unique-id") or "").strip()
    if vid and user:
        return f"https://www.tiktok.com/@{user}/video/{vid}"
    if cite:
        return cite.split("?")[0]
    return None

def canon_tiktok_from_iframe(src: str, ancestor_blockquote=None) -> Optional[str]:
    if ancestor_blockquote:
        cu = canon_tiktok_from_blockquote(ancestor_blockquote)
        if cu:
            return cu
    src = (src or "").strip()
    m = TT_EMBED_ID.search(src)
    if m:
        return f"https://www.tiktok.com/embed/v2/{m.group(1)}"
    return None

def is_visible(el, min_w=40, min_h=40) -> bool:
    try:
        rect = el.rect or {}
        w, h = rect.get("width", 0), rect.get("height", 0)
        return w >= min_w and h >= min_h and el.is_displayed()
    except Exception:
        return False


CONSENT_TEXTS = [
    "Tout accepter","J’accepte","J'accepte","Accepter",
    "Continuer sans consentir","Continuer sans accepter",
    "Tout refuser","Refuser tout","Rejeter",
    "OK","I accept","Agree","Accept all","Reject all"
]

def _click_first(driver, by, sel) -> bool:
    try:
        for el in driver.find_elements(by, sel):
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.1)
                el.click()
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def try_consent(driver, timeout=12) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        for t in CONSENT_TEXTS:
            xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
            if _click_first(driver, By.XPATH, xp):
                return True
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for fr in frames:
            try:
                driver.switch_to.frame(fr)
                for t in CONSENT_TEXTS:
                    xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
                    if _click_first(driver, By.XPATH, xp):
                        driver.switch_to.default_content()
                        return True
                driver.switch_to.default_content()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                continue
        time.sleep(0.4)
    return False


def make_driver(headless: bool = True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=fr-FR,fr;q=0.9,en;q=0.8")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    return webdriver.Chrome(options=opts)

def slow_scroll(driver, steps=24, dy=1500, pause=0.25):
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
        time.sleep(pause)


def extract_visible_video_links(url: str, headless: bool = True) -> List[str]:
    driver = make_driver(headless=headless)
    links: List[str] = []
    seen = set()

    try:
        driver.get(url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        try_consent(driver, timeout=14)
        time.sleep(0.5)
        slow_scroll(driver, steps=26, dy=1600, pause=0.22)
        time.sleep(0.6)

        candidates = driver.find_elements(By.CSS_SELECTOR, "article, main, .article, .page-content-wrapper, body")
        root = candidates[0] if candidates else driver.find_element(By.TAG_NAME, "body")

        y_iframes = root.find_elements(By.CSS_SELECTOR,
            "iframe[src*='youtube.com/embed'], iframe[src*='youtube-nocookie.com/embed'], "
            "iframe[data-src*='youtube.com/embed'], iframe[data-src*='youtube-nocookie.com/embed']"
        )
        for fr in y_iframes:
            if not is_visible(fr):
                continue
            src = (fr.get_attribute("src") or fr.get_attribute("data-src") or "").strip()
            cu = canon_youtube(src)
            if cu and cu not in seen:
                seen.add(cu); links.append(cu)
            srcdoc = (fr.get_attribute("srcdoc") or "").strip()
            if srcdoc:
                html = unescape(srcdoc)
                for m in Y_EMBED.finditer(html):
                    cu2 = _add_qs(f"https://www.youtube.com/embed/{m.group(1)}", {"autoplay":"1"})
                    if cu2 not in seen:
                        seen.add(cu2); links.append(cu2)

        bqs = root.find_elements(By.CSS_SELECTOR, "blockquote.tiktok-embed")
        for bq in bqs:
            if not is_visible(bq, 20, 20):
                inner_ifrs = bq.find_elements(By.CSS_SELECTOR, "iframe[src*='tiktok.com/embed/']")
                if not any(is_visible(x, 20, 20) for x in inner_ifrs):
                    continue
            cu = canon_tiktok_from_blockquote(bq)
            if cu and cu not in seen:
                seen.add(cu); links.append(cu)

        tt_ifr = root.find_elements(By.CSS_SELECTOR, "iframe[src*='tiktok.com/embed/']")
        for fr in tt_ifr:
            if not is_visible(fr, 20, 20):
                continue
            src = (fr.get_attribute("src") or "").strip()
            ancestor = None
            try:
                ancestor = fr.find_element(By.XPATH, "ancestor::blockquote[contains(@class,'tiktok-embed')]")
            except Exception:
                pass
            cu = canon_tiktok_from_iframe(src, ancestor_blockquote=ancestor)
            if cu and cu not in seen:
                seen.add(cu); links.append(cu)

        return sorted(links)

    finally:
        driver.quit()


def handle(review_url: str):
    try:
        return extract_visible_video_links(review_url, headless=True)
    except Exception:
        return []
