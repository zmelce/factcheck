
import re
import time
from html import unescape
from typing import List, Dict
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException

Y_EMBED  = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{6,})", re.I)

DM_PLAYER = re.compile(r"https?://(?:geo\.)?dailymotion\.com/player/[^\"'>?]+(?:\?[^\"'>#]*)?\bvideo=([A-Za-z0-9]+)", re.I)
DM_EMBED  = re.compile(r"https?://(?:www\.)?dailymotion\.com/embed/video/([A-Za-z0-9]+)", re.I)

TIKTOK_EMBED = re.compile(r"https?://(?:www\.)?tiktok\.com/embed/(?:v2/)?(\d+)", re.I)

TW_EMBED = re.compile(r"https?://platform\.twitter\.com/embed/Tweet\.html\?[^\"'>]+", re.I)
TW_ID    = re.compile(r"(?:\?|&)id=(\d+)(?:&|$)")

RAW_VIDEO = re.compile(r"https?://[^\"'>]+\.(?:mp4|m3u8)(?:\?[^\"'>]*)?", re.I)

WRAPPER_CSS = "div.page-content-wrapper"

def _abs_http(u: str) -> str:
    return ("https:" + u) if u and u.startswith("//") else (u or "")

def _strip_qs_frag(u: str) -> str:
    sp = urlsplit(u)
    return urlunsplit((sp.scheme, sp.netloc, sp.path, "", ""))

def _add_query(u: str, extra: dict) -> str:
    sp = urlsplit(u)
    qs = dict(parse_qsl(sp.query))
    qs.update(extra or {})
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(qs), sp.fragment))

def _dedupe_keep_shortest_prefix(urls):
    urls = sorted(set(urls), key=lambda x: (len(x), x))
    out = []
    for u in urls:
        if any(u.startswith(k) for k in out):
            continue
        out = [k for k in out if not k.startswith(u)]
        out.append(u)
    return out

def canon_youtube_embed(u: str) -> str | None:
    u = _abs_http(u)
    m = Y_EMBED.search(u)
    if not m:
        return None
    vid = m.group(1)
    return _add_query(f"https://www.youtube.com/embed/{vid}", {"autoplay": "1"})

def canon_dailymotion_embed(u: str) -> str | None:
    u = _abs_http(u)
    m = DM_PLAYER.search(u) or DM_EMBED.search(u)
    return f"https://www.dailymotion.com/embed/video/{m.group(1)}" if m else None

def canon_tiktok_embed(u: str) -> str | None:
    u = _abs_http(u)
    m = TIKTOK_EMBED.search(u)
    if not m:
        return None
    vid = m.group(1)
    return f"https://www.tiktok.com/embed/v2/{vid}"

def canon_twitter_status_from_iframe(u: str) -> str | None:
    u = _abs_http(u)
    if not TW_EMBED.search(u):
        return None
    m = TW_ID.search(u)
    return f"https://x.com/i/web/status/{m.group(1)}" if m else None

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

def _click_first(driver, by, sel) -> bool:
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
            except Exception:
                continue
    except Exception:
        pass
    return False

def _try_plain_dom_consent(driver) -> bool:
    for t in CONSENT_TEXTS:
        xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
        if _click_first(driver, By.XPATH, xp):
            return True
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
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
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
    function findDidomiAndClick() {
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
    }
    return findDidomiAndClick();
    """
    try:
        return bool(driver.execute_script(js, CONSENT_TEXTS))
    except WebDriverException:
        return False

def accept_all_consents(driver, timeout=15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _try_plain_dom_consent(driver): return True
        if _try_iframe_consent(driver):    return True
        if _try_didomi_shadow_consent(driver): return True
        time.sleep(0.4)
    return False

def _is_visible_player(driver, el, min_w=40, min_h=40) -> bool:
    try:
        if not el.is_displayed():
            return False
        rect = driver.execute_script(
            "const r=arguments[0].getBoundingClientRect();return [r.width,r.height,r.top,r.left];",
            el
        )
        w, h = float(rect[0] or 0), float(rect[1] or 0)
        if w < min_w or h < min_h:
            return False
        style = (el.get_attribute("style") or "").lower()
        if "display:none" in style or "visibility:hidden" in style or "opacity: 0" in style or "opacity:0" in style:
            return False
        return True
    except Exception:
        return False

def extract_embeds_from_srcdoc(srcdoc: str):
    res = {"youtube": [], "dailymotion": [], "tiktok": [], "twitter": [], "raw_video": []}
    if not srcdoc:
        return res
    html = unescape(srcdoc)

    for m in Y_EMBED.finditer(html):
        res["youtube"].append(_add_query(f"https://www.youtube.com/embed/{m.group(1)}", {"autoplay": "1"}))
    for m in DM_PLAYER.finditer(html):
        res["dailymotion"].append(f"https://www.dailymotion.com/embed/video/{m.group(1)}")
    for m in DM_EMBED.finditer(html):
        res["dailymotion"].append(f"https://www.dailymotion.com/embed/video/{m.group(1)}")
    for m in TIKTOK_EMBED.finditer(html):
        res["tiktok"].append(f"https://www.tiktok.com/embed/v2/{m.group(1)}")
    for m in RAW_VIDEO.finditer(html):
        res["raw_video"].append(_strip_qs_frag(m.group(0)))

    for k in res:
        res[k] = _dedupe_keep_shortest_prefix(res[k])
    return res

def extract_from_wrapper(driver) -> dict[str, list[str]]:
    try:
        wrapper = driver.find_element(By.CSS_SELECTOR, WRAPPER_CSS)
    except Exception:
        return {}

    buckets = {"youtube": [], "dailymotion": [], "tiktok": [], "twitter": [], "raw_video": []}

    iframes = wrapper.find_elements(By.CSS_SELECTOR, "iframe[src]")
    for ifr in iframes:
        if not _is_visible_player(driver, ifr):
            continue
        src = _abs_http((ifr.get_attribute("src") or "").strip())
        if not src:
            continue

        cu = (canon_youtube_embed(src) or
              canon_dailymotion_embed(src) or
              canon_tiktok_embed(src))

        if cu:
            if cu.startswith("https://www.youtube.com/embed/"): buckets["youtube"].append(cu)
            elif cu.startswith("https://www.dailymotion.com/embed/video/"): buckets["dailymotion"].append(cu)
            elif cu.startswith("https://www.tiktok.com/embed/"): buckets["tiktok"].append(cu)
        else:
            tw = canon_twitter_status_from_iframe(src)
            if tw:
                buckets["twitter"].append(tw)
            elif RAW_VIDEO.search(src):
                buckets["raw_video"].append(_strip_qs_frag(src))

        srcdoc = (ifr.get_attribute("srcdoc") or "").strip()
        if srcdoc:
            sub = extract_embeds_from_srcdoc(srcdoc)
            for k in buckets:
                buckets[k].extend(sub.get(k, []))

    videos = wrapper.find_elements(By.CSS_SELECTOR, "video")
    for vid in videos:
        if not _is_visible_player(driver, vid):
            continue
        vsrc = _abs_http((vid.get_attribute("src") or "").strip())
        if vsrc and RAW_VIDEO.search(vsrc):
            buckets["raw_video"].append(_strip_qs_frag(vsrc))
        for s in vid.find_elements(By.CSS_SELECTOR, "source[src]"):
            ssrc = _abs_http((s.get_attribute("src") or "").strip())
            if ssrc and RAW_VIDEO.search(ssrc):
                buckets["raw_video"].append(_strip_qs_frag(ssrc))

    for k, v in buckets.items():
        buckets[k] = _dedupe_keep_shortest_prefix(v)

    return {k: v for k, v in buckets.items() if v}

def handle(review_url: str, headless: bool = True) -> List[str]:
    driver = make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        accept_all_consents(driver, timeout=18)
        time.sleep(0.6)

        for _ in range(18):
            driver.execute_script("window.scrollBy(0, 1400);")
            time.sleep(0.2)

        results = extract_from_wrapper(driver)  # dict[str, list[str]]
        if not results:
            return []

        flat: List[str] = []
        for k in ("youtube", "dailymotion", "tiktok", "twitter", "raw_video"):
            flat.extend(results.get(k, []))
        return sorted(set(flat))
    except Exception:
        return []
    finally:
        driver.quit()
