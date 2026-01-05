
import re
import time
import json
import base64
from html import unescape
from typing import List
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl, parse_qs, unquote

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException

__all__ = ["extract_links", "handle"]


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


WRAPPER_XPATH = "//div[contains(@class,'EmbedInstagram__Wrapper')]"

SOCIAL_PATTERNS = [
    re.compile(r"https?://(?:www\.)?instagram\.com/(?:reel|p)/[A-Za-z0-9_-]+/?", re.I),
    re.compile(r"https?://(?:www\.)?instagram\.com/p/[^\"'>]+/embed/[^\"'>]*", re.I),
    re.compile(r"https?://[^\"'>]*cdninstagram\.com/[^\"'>]+\.mp4[^\"'>]*", re.I),
    re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[^/]+/status/\d+", re.I),
    re.compile(r"https?://(?:www\.)?facebook\.com/(?:watch/\?v=\d+|[^/]+/videos/\d+/?).*", re.I),
    re.compile(r"https?://[^\"'>]+\.mp4(?:\?[^\"'>]*)?", re.I),
    re.compile(r"https?://(?:www\.)?tiktok\.com/@[^/]+/video/\d+", re.I),
    re.compile(r"https?://(?:www\.)?tiktok\.com/embed/(?:v2/)?\d+", re.I),
    re.compile(r"https?://(?:v(?:m|t)|www)\.tiktok\.com/[^\"'>]+", re.I),
]

def sanitize_provider_url(u: str) -> str:
    try:
        sp = urlsplit(u)
        qs = dict(parse_qsl(sp.query))
        for k in list(qs.keys()):
            if k.lower() in {"rd", "rp", "cr", "wp", "v"}:
                qs.pop(k, None)
        path = sp.path
        if ("instagram.com/p/" in u or "instagram.com/reel/" in u) and not path.endswith("/"):
            path += "/"
        return urlunsplit((sp.scheme, sp.netloc, path, urlencode(qs), ""))  # drop fragments
    except Exception:
        return u

def extract_urls_matching(html: str, patterns) -> list[str]:
    found = set()
    for attr in ("src", "href"):
        for u in re.findall(fr'{attr}="([^"]+)"', html, flags=re.I):
            for p in patterns:
                if p.search(u):
                    found.add(u); break
        for u in re.findall(fr"{attr}='([^']+)'", html, flags=re.I):
            for p in patterns:
                if p.search(u):
                    found.add(u); break
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
    "ACCEPTER ET FERMER", "Tout accepter", "J’accepte", "J'accepte", "Accepter",
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

def _click_first(driver, by, sel) -> bool:
    try:
        els = driver.find_elements(by, sel)
        if not els: return False
        for el in els:
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

def _try_plain_dom_consent(driver) -> bool:
    for t in CONSENT_TEXTS:
        xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
        if _click_first(driver, By.XPATH, xp): return True
    for t in CONSENT_TEXTS:
        xp = f"//*[self::a or self::span][normalize-space()='{t}' or contains(., '{t}')]"
        if _click_first(driver, By.XPATH, xp): return True
    return False

def _try_iframe_consent(driver) -> bool:
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for fr in frames:
        try:
            driver.switch_to.frame(fr)
            if _try_plain_dom_consent(driver):
                driver.switch_to.default_content()
                return True
            driver.switch_to.default_content()
        except Exception:
            try: driver.switch_to.default_content()
            except Exception: pass
            continue
    return False

def _try_didomi_shadow_consent(driver) -> bool:
    js = """
    const TEXTS = arguments[0];
    function searchShadow(root) {
      const tryClick = (el) => { try { el.click(); return true; } catch(e) { return false; } };
      const nodes = root.querySelectorAll('button, [role="button"], *');
      for (const n of nodes) {
        const txt = (n.textContent||'').trim();
        if (!txt) continue;
        for (const t of TEXTS) { if (txt.includes(t)) return tryClick(n); }
      }
      return false;
    }
    const host = document.querySelector('#didomi-host');
    if (!host) return false;
    const roots = [];
    if (host.shadowRoot) roots.push(host.shadowRoot);
    const all = host.querySelectorAll('*');
    for (const el of all) if (el.shadowRoot) roots.push(el.shadowRoot);
    for (const r of roots) {
      if (searchShadow(r)) return true;
      const deep = r.querySelectorAll('*');
      for (const d of deep) if (d.shadowRoot && searchShadow(d.shadowRoot)) return true;
    }
    return false;
    """
    try:
        return bool(driver.execute_script(js, CONSENT_TEXTS))
    except WebDriverException:
        return False

def accept_all_consents(driver, timeout=18) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _try_plain_dom_consent(driver): return True
        if _try_iframe_consent(driver):    return True
        if _try_didomi_shadow_consent(driver): return True
        time.sleep(0.4)
    return False


def slow_scroll(driver, steps=18, dy=1600, pause=0.22):
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
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


def extract_ultimedia_iframes(driver):
    urls = []
    sel = "div.c-media__content.c-media__content--video iframe"
    for fr in driver.find_elements(By.CSS_SELECTOR, sel):
        src = (fr.get_attribute("src") or "").strip()
        if src and "ultimedia.com/deliver/generic/iframe" in src:
            urls.append(src)
    return urls


_FEATURE_VIDEO_PAT = re.compile(r"(video|hls|player|mixed[_-]?media)", re.I)

def _b64json_decode(s: str) -> dict:
    try:
        u = unquote(s)
        if "%3D" in u or "%2F" in u or "%2B" in u:
            u = unquote(u)
        pad = (-len(u)) % 4
        u += "=" * pad
        data = base64.urlsafe_b64decode(u.encode("utf-8"))
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {}

def _looks_like_video(features: dict) -> bool:
    if not isinstance(features, dict):
        return False
    stack = [features]
    keys = []
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                keys.append(str(k))
                if isinstance(v, (dict, list)): stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)): stack.append(v)
    return bool(_FEATURE_VIDEO_PAT.search(" ".join(keys)))

def _tweet_owner_from_features(features: dict) -> str | None:
    if not isinstance(features, dict): return None
    stack = [features]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for _, v in cur.items():
                if isinstance(v, str) and v.startswith("@"): return v.lstrip("@")
                if isinstance(v, (dict, list)): stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, str) and v.startswith("@"): return v.lstrip("@")
                if isinstance(v, (dict, list)): stack.append(v)
    return None

def build_x_url(tweet_id: str, owner: str | None) -> str:
    return f"https://x.com/{owner}/status/{tweet_id}" if owner else f"https://x.com/i/web/status/{tweet_id}"

def extract_x_video_links(driver, wait_up_to=12):
    try:
        WebDriverWait(driver, wait_up_to).until(
            EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "div.twitter-tweet iframe[src*='platform.twitter.com/embed/Tweet.html']")
            )
        )
    except TimeoutException:
        pass

    out = []
    wrappers = driver.find_elements(By.CSS_SELECTOR, "div.twitter-tweet")
    for w in wrappers:
        iframes = w.find_elements(By.CSS_SELECTOR, "iframe[src*='platform.twitter.com/embed/Tweet.html']")
        for fr in iframes:
            src = (fr.get_attribute("src") or "").strip()
            if not src:
                continue
            q = parse_qs(urlsplit(src).query)
            tweet_id = (q.get("id", [None])[0]
                        or fr.get_attribute("data-tweet-id")
                        or w.get_attribute("data-tweet-id"))
            if not tweet_id or not re.fullmatch(r"\d{5,}", tweet_id):
                continue
            features = {}
            if "features" in q and q["features"]:
                features = _b64json_decode(q["features"][0])
            if not features:
                title = (fr.get_attribute("title") or "").lower()
                if "video" not in title:
                    continue
            if features and not _looks_like_video(features):
                continue
            owner = _tweet_owner_from_features(features)
            out.append(build_x_url(tweet_id, owner))
    return out


TIKTOK_CANONICAL_RE = re.compile(r"https?://(?:www\.)?tiktok\.com/@([^/]+)/video/(\d+)", re.I)
TIKTOK_EMBED_ID_RE  = re.compile(r"/embed/(?:v2/)?(\d+)", re.I)

def _clean_url(u: str) -> str:
    sp = urlsplit(u)
    return urlunsplit((sp.scheme, sp.netloc, sp.path, "", ""))  # drop query & fragment

def extract_tiktok_links(driver):
    found = []

    blocks = driver.find_elements(By.CSS_SELECTOR, "blockquote.tiktok-embed")
    for bq in blocks:
        try:
            cite = (bq.get_attribute("cite") or "").strip()
            if cite:
                m = TIKTOK_CANONICAL_RE.search(cite)
                if m:
                    found.append(_clean_url(m.group(0)))
                    continue
            vid = (bq.get_attribute("data-video-id") or "").strip()
            user = (bq.get_attribute("data-unique-id") or "").strip()
            if vid and user:
                found.append(f"https://www.tiktok.com/@{user}/video/{vid}")
                continue
            ifr = bq.find_elements(By.CSS_SELECTOR, "iframe[src*='tiktok.com/embed/']")
            for fr in ifr:
                src = (fr.get_attribute("src") or "").strip()
                m2 = TIKTOK_EMBED_ID_RE.search(src)
                if m2:
                    if vid and user:
                        found.append(f"https://www.tiktok.com/@{user}/video/{vid}")
                    elif cite:
                        found.append(_clean_url(cite))
                    else:
                        found.append(_clean_url(src))
        except Exception:
            continue

    stray_iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='tiktok.com/embed/']")
    for fr in stray_iframes:
        try:
            src = (fr.get_attribute("src") or "").strip()
            if not src:
                continue
            m2 = TIKTOK_EMBED_ID_RE.search(src)
            if not m2:
                continue
            vid = m2.group(1)
            user = None
            cite = None
            try:
                ancestor = fr.find_element(By.XPATH, "ancestor::blockquote[contains(@class,'tiktok-embed')]")
                cite = (ancestor.get_attribute("cite") or "").strip()
                if cite:
                    m = TIKTOK_CANONICAL_RE.search(cite)
                    if m:
                        found.append(_clean_url(m.group(0)))
                        continue
                user = (ancestor.get_attribute("data-unique-id") or "").strip()
            except Exception:
                pass
            if user:
                found.append(f"https://www.tiktok.com/@{user}/video/{vid}")
            else:
                found.append(_clean_url(src))
        except Exception:
            continue

    return dedupe_by_prefix_keep_shortest(found)


def _extract_all(page_source: str, driver):
    SENTINEL_END = None  # kept to match original behavior

    yt_urls = set()
    iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='youtube.com/embed'],iframe[src*='youtube-nocookie.com/embed']")
    for fr in iframes:
        src = fr.get_attribute("src") or ""
        if Y_EMBED.search(src):
            yt_urls.add(add_autoplay(src))
        srcdoc = fr.get_attribute("srcdoc") or ""
        for u in extract_from_srcdoc(srcdoc):
            if Y_EMBED.search(u):
                yt_urls.add(add_autoplay(u))

    if not yt_urls:
        html = truncate_at_sentinel(page_source or "", SENTINEL_END)
        for m in Y_EMBED.finditer(html):
            yt_urls.add(add_autoplay(m.group(0)))
        for href in re.findall(r'href="([^"]+)"', html, flags=re.I):
            if Y_EMBED.search(href):
                yt_urls.add(add_autoplay(href))
    yt_urls = sorted(set(yt_urls))

    ult_urls = extract_ultimedia_iframes(driver)

    limited_html = truncate_at_sentinel(page_source or "", SENTINEL_END)
    social_candidates = []
    wrappers = driver.find_elements(By.XPATH, WRAPPER_XPATH)
    for w in wrappers:
        try:
            outer = w.get_attribute("outerHTML") or ""
            if SENTINEL_END and outer and (outer not in limited_html):
                continue

            nodes = w.find_elements(By.XPATH, ".//iframe|.//a|.//video|.//source")
            for n in nodes:
                for attr in ("src", "href"):
                    val = (n.get_attribute(attr) or "").strip()
                    if val:
                        social_candidates.append(val)
                srcdoc = n.get_attribute("srcdoc") or ""
                if srcdoc:
                    social_candidates += extract_urls_matching(unescape(srcdoc), SOCIAL_PATTERNS)

            social_candidates += extract_urls_matching(outer, SOCIAL_PATTERNS)
        except Exception:
            continue

    insta_urls = [u for u in social_candidates if "instagram.com" in u]
    insta_urls = dedupe_by_prefix_keep_shortest(insta_urls)

    x_urls = extract_x_video_links(driver)

    tiktok_urls = extract_tiktok_links(driver)

    combined = yt_urls + list(set(ult_urls)) + insta_urls + list(set(x_urls)) + tiktok_urls
    return sorted(set(combined))


def extract_links(review_url: str, headless: bool = True) -> List[str]:
    driver = make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 22).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        accept_all_consents(driver, timeout=18)
        time.sleep(0.8)

        slow_scroll(driver, steps=18, dy=1600, pause=0.22)
        time.sleep(0.6)

        return _extract_all(driver.page_source or "", driver)
    except Exception:
        return []
    finally:
        driver.quit()

def handle(review_url: str) -> List[str]:
    return extract_links(review_url, headless=True)
