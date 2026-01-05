
import re
import time
from html import unescape
from typing import List
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException


Y_EMBED = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})",
    re.I,
)

def add_autoplay(u: str) -> str:
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs["autoplay"] = "1"
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(qs), sp.fragment))

def poster_url(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

def anchor_from_embed(embed_url: str) -> str | None:
    m = Y_EMBED.search(embed_url or "")
    if not m:
        return None
    vid = m.group(1)
    href = add_autoplay(f"https://www.youtube.com/embed/{vid}")
    return f'<a href="{href}"><img src="{poster_url(vid)}" alt=""><span>▶</span></a>'

def href_from_anchor(a: str) -> str | None:
    m = re.search(r'href="([^"]+)"', a)
    return m.group(1) if m else None


WRAPPER_XPATH = "//div[contains(@class,'EmbedInstagram__Wrapper')]"

SOCIAL_PATTERNS = [
    re.compile(r"https?://(?:www\.)?instagram\.com/(?:reel|p)/[A-Za-z0-9_-]+/?", re.I),
    re.compile(r"https?://(?:www\.)?instagram\.com/p/[^\"'>]+/embed/[^\"'>]*", re.I),
    re.compile(r"https?://[^\"'>]*cdninstagram\.com/[^\"'>]+\.mp4[^\"'>]*", re.I),
    re.compile(r"https?://(?:www\.)?tiktok\.com/@[^/]+/video/\d+", re.I),
    re.compile(r"https?://(?:v(?:m|t)|www)\.tiktok\.com/[^\"'>]+", re.I),
    re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[^/]+/status/\d+", re.I),
    re.compile(r"https?://(?:www\.)?facebook\.com/(?:watch/\?v=\d+|[^/]+/videos/\d+/?).*", re.I),
    re.compile(r"https?://[^\"'>]+\.mp4(?:\?[^\"'>]*)?", re.I),
]

def sanitize_provider_url(u: str) -> str:
    try:
        sp = urlsplit(u)
        qs = dict(parse_qsl(sp.query))
        for k in list(qs.keys()):
            if k.lower() in {"rd", "rp", "cr", "wp", "v"}:
                del qs[k]
        path = sp.path
        if ("instagram.com/p/" in u or "instagram.com/reel/" in u) and not path.endswith("/"):
            path += "/"
        return urlunsplit((sp.scheme, sp.netloc, path, urlencode(qs), ""))
    except:
        return u

def extract_urls_matching(html: str, patterns) -> list[str]:
    found = set()
    for attr in ("src", "href"):
        for u in re.findall(fr'{attr}="([^"]+)"', html, flags=re.I):
            for p in patterns:
                if p.search(u):
                    found.add(u)
                    break
        for u in re.findall(fr"{attr}='([^']+)'", html, flags=re.I):
            for p in patterns:
                if p.search(u):
                    found.add(u)
                    break
    for p in patterns:
        for m in p.finditer(html):
            found.add(m.group(0))
    return sorted(found)

def dedupe_by_prefix_keep_shortest(urls: list[str]) -> list[str]:
    norm = [sanitize_provider_url(u) for u in urls]
    norm = sorted(set(norm), key=lambda x: (len(x), x))
    kept = []
    for u in norm:
        if any(u.startswith(k) for k in kept):
            continue
        kept = [k for k in kept if not k.startswith(u)]
        kept.append(u)
    return kept


CONSENT_TEXTS = [
    "Tout accepter", "J’accepte", "J'accepte", "Accepter",
    "Continuer sans consentir", "Continuer sans accepter",
    "Tout refuser", "Refuser tout", "Rejeter",
    "OK", "J’accept", "J'accept", "I accept (for free)"
]

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
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    return webdriver.Chrome(options=opts)

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


def slow_scroll(driver, steps=24, dy=1600, pause=0.2):
    for _ in range(steps):
        ActionChains(driver).scroll_by_amount(0, dy).perform()
        time.sleep(pause)

def extract_from_srcdoc(srcdoc: str) -> list[str]:
    urls = []
    if not srcdoc:
        return urls
    html = unescape(srcdoc)
    for m in Y_EMBED.finditer(html):
        urls.append(m.group(0))
    for href in re.findall(r'href="([^"]+)"', html, flags=re.I):
        if Y_EMBED.search(href):
            urls.append(href)
    for href in re.findall(r"href='([^']+)'", html, flags=re.I):
        if Y_EMBED.search(href):
            urls.append(href)
    urls += extract_urls_matching(html, SOCIAL_PATTERNS)
    return urls

def truncate_at_sentinel(full_html: str, sentinel: str | None) -> str:
    if not sentinel:
        return full_html
    idx = full_html.lower().find(sentinel.lower())
    return full_html if idx == -1 else full_html[:idx]


def handle(review_url: str, headless: bool = True) -> List[str]:
    SENTINEL_END = None

    driver = make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        accept_all_consents(driver, timeout=18)
        time.sleep(0.8)

        slow_scroll(driver, steps=24, dy=1600, pause=0.2)
        time.sleep(0.6)

        yt_urls = set()

        iframes = driver.find_elements(
            By.CSS_SELECTOR,
            "iframe[src*='youtube.com/embed'],iframe[src*='youtube-nocookie.com/embed']"
        )
        for fr in iframes:
            src = fr.get_attribute("src") or ""
            if Y_EMBED.search(src):
                yt_urls.add(add_autoplay(src))
            srcdoc = fr.get_attribute("srcdoc") or ""
            for u in extract_from_srcdoc(srcdoc):
                if Y_EMBED.search(u):
                    yt_urls.add(add_autoplay(u))

        if not yt_urls:
            html = truncate_at_sentinel(driver.page_source or "", SENTINEL_END)
            for m in Y_EMBED.finditer(html):
                yt_urls.add(add_autoplay(m.group(0)))
            for href in re.findall(r'href="([^"]+)"', html, flags=re.I):
                if Y_EMBED.search(href):
                    yt_urls.add(add_autoplay(href))

        yt_urls = sorted(set(yt_urls))

        limited_html = truncate_at_sentinel(driver.page_source or "", SENTINEL_END)
        social_candidates = []

        wrappers = driver.find_elements(By.XPATH, WRAPPER_XPATH)
        for w in wrappers:
            try:
                outer = w.get_attribute("outerHTML") or ""
                if SENTINEL_END and outer and (outer not in limited_html):
                    continue

                nodes = []
                nodes += w.find_elements(By.XPATH, ".//iframe|.//a|.//video|.//source")
                for n in nodes:
                    for attr in ("src", "href"):
                        val = (n.get_attribute(attr) or "").strip()
                        if not val:
                            continue
                        social_candidates.append(val)
                    srcdoc = n.get_attribute("srcdoc") or ""
                    if srcdoc:
                        social_candidates += extract_urls_matching(unescape(srcdoc), SOCIAL_PATTERNS)

                social_candidates += extract_urls_matching(outer, SOCIAL_PATTERNS)

            except:
                continue

        social_urls = dedupe_by_prefix_keep_shortest(social_candidates)

        flat: List[str] = []
        flat.extend(yt_urls)
        flat.extend(social_urls)
        return sorted(set(flat))
    except:
        return []
    finally:
        driver.quit()
