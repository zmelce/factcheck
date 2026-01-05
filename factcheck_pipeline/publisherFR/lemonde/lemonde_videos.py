import re
import time
from html import unescape
from typing import List
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import WebDriverException


Y_EMBED = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})",
    re.I,
)

def yt_clean_embed(u: str) -> str | None:
    m = Y_EMBED.search(u or "")
    if not m:
        return None
    vid = m.group(1)
    return f"https://www.youtube.com/embed/{vid}"

def yt_add_autoplay(u: str) -> str:
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs["autoplay"] = "1"
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(qs), sp.fragment))


DM_PLAYER = re.compile(
    r"https?://(?:geo\.)?dailymotion\.com/player/[^\"'>?]+(?:\?[^\"'>#]*)?\bvideo=([A-Za-z0-9]+)",
    re.I,
)
DM_EMBED = re.compile(
    r"https?://(?:www\.)?dailymotion\.com/embed/video/([A-Za-z0-9]+)",
    re.I,
)
DM_WATCH = re.compile(
    r"https?://(?:www\.)?dailymotion\.com/video/([A-Za-z0-9]+)",
    re.I,
)

def dm_clean_embed(u: str) -> str | None:
    s = u or ""
    m = DM_PLAYER.search(s) or DM_EMBED.search(s) or DM_WATCH.search(s)
    if not m:
        return None
    vid = m.group(1)
    return f"https://www.dailymotion.com/embed/video/{vid}"


CONSENT_TEXTS = [
    "Tout accepter", "J'accepte", "J'accepte", "Accepter",
    "Continuer sans consentir", "Continuer sans accepter",
    "Tout refuser", "Refuser tout", "Rejeter",
    "OK", "J'accept", "J'accept", "I accept (for free)"
]

CHROMEDRIVER_PATH = "/Users/melce/bin/chromedriver"

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
    opts.add_argument("--remote-debugging-port=0")
    service = Service(
        executable_path=CHROMEDRIVER_PATH,
        port=0,
        log_output="chromedriver_videos.log"
    )
    return webdriver.Chrome(service=service, options=opts)

def click_first(driver, by, sel) -> bool:
    try:
        els = driver.find_elements(by, sel)
        if not els:
            return False
        for el in els:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.1)
                el.click()
                return True
            except:
                continue
    except:
        pass
    return False

def try_plain_dom_consent(driver) -> bool:
    for t in CONSENT_TEXTS:
        xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
        if click_first(driver, By.XPATH, xp):
            return True
    return False

def try_iframe_consent(driver) -> bool:
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for fr in frames:
        try:
            driver.switch_to.frame(fr)
            if try_plain_dom_consent(driver):
                driver.switch_to.default_content()
                return True
            driver.switch_to.default_content()
        except:
            try:
                driver.switch_to.default_content()
            except:
                pass
            continue
    return False

def try_didomi_shadow_consent(driver) -> bool:
    try:
        host = driver.find_element(By.CSS_SELECTOR, "#didomi-host")
    except:
        return False

    roots_to_search = []
    try:
        roots_to_search.append(host.shadow_root)
    except:
        pass

    for el in host.find_elements(By.CSS_SELECTOR, "*"):
        try:
            roots_to_search.append(el.shadow_root)
        except:
            continue

    for shadow in roots_to_search:
        try:
            candidates = shadow.find_elements(By.CSS_SELECTOR, "button, [role='button']")
            for btn in candidates:
                txt = (btn.text or "").strip()
                if not txt:
                    continue
                for t in CONSENT_TEXTS:
                    if t in txt:
                        try:
                            btn.click()
                            return True
                        except:
                            continue
        except:
            continue

    return False

def accept_all_consents(driver, timeout=15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if try_plain_dom_consent(driver): return True
        if try_iframe_consent(driver):    return True
        if try_didomi_shadow_consent(driver): return True
        time.sleep(0.4)
    return False

def slow_scroll(driver, steps=20, dy=1400, pause=0.2):
    ac = ActionChains(driver)
    for _ in range(steps):
        ac.scroll_by_amount(0, dy).perform()
        time.sleep(pause)

def extract_embed_urls_from_html(html: str) -> tuple[list[str], list[str]]:
    if not html:
        return [], []
    html_u = unescape(html)

    yt = set()
    for m in Y_EMBED.finditer(html_u):
        yt.add(f"https://www.youtube.com/embed/{m.group(1)}")
    for href in re.findall(r'href="([^"]+)"', html_u, flags=re.I):
        emb = yt_clean_embed(href)
        if emb:
            yt.add(emb)
    for href in re.findall(r"href='([^']+)'", html_u, flags=re.I):
        emb = yt_clean_embed(href)
        if emb:
            yt.add(emb)

    dm = set()
    for m in DM_PLAYER.finditer(html_u):
        dm.add(f"https://www.dailymotion.com/embed/video/{m.group(1)}")
    for m in DM_EMBED.finditer(html_u):
        dm.add(f"https://www.dailymotion.com/embed/video/{m.group(1)}")
    for m in DM_WATCH.finditer(html_u):
        dm.add(f"https://www.dailymotion.com/embed/video/{m.group(1)}")
    for href in re.findall(r'href="([^"]+)"', html_u, flags=re.I):
        emb = dm_clean_embed(href)
        if emb:
            dm.add(emb)
    for href in re.findall(r"href='([^']+)'", html_u, flags=re.I):
        emb = dm_clean_embed(href)
        if emb:
            dm.add(emb)

    return sorted(yt), sorted(dm)


def extract_links(review_url: str, headless: bool = True) -> List[str]:
    driver = make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        accept_all_consents(driver, timeout=18)
        time.sleep(0.6)

        slow_scroll(driver, steps=20, dy=1400, pause=0.2)
        time.sleep(0.4)

        yt_embeds, dm_embeds = set(), set()

        iframe_css = (
            "iframe[src*='youtube.com/embed'],"
            "iframe[src*='youtube-nocookie.com/embed'],"
            "iframe[src*='dailymotion.com/player/'],"
            "iframe[src*='dailymotion.com/embed/video/'],"
            "iframe.article__video-element,iframe.js_player,iframe.dailymotion-player"
        )
        iframes = driver.find_elements(By.CSS_SELECTOR, iframe_css)

        for fr in iframes:
            src = (fr.get_attribute("src") or "").strip()
            if src:
                y = yt_clean_embed(src)
                if y:
                    yt_embeds.add(y)
                d = dm_clean_embed(src)
                if d:
                    dm_embeds.add(d)

            srcdoc = (fr.get_attribute("srcdoc") or "").strip()
            if srcdoc:
                ys, ds = extract_embed_urls_from_html(srcdoc)
                yt_embeds.update(ys)
                dm_embeds.update(ds)

        if not yt_embeds or not dm_embeds:
            ys, ds = extract_embed_urls_from_html(driver.page_source or "")
            if not yt_embeds:
                yt_embeds.update(ys)
            if not dm_embeds:
                dm_embeds.update(ds)

        all_links = sorted(set(yt_embeds) | set(dm_embeds))
        return all_links

    except:
        return []
    finally:
        driver.quit()

def handle(review_url: str, headless: bool = True) -> List[str]:
    return extract_links(review_url, headless=headless)
