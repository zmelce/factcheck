import hashlib
import os
import re
import time
from io import BytesIO
from urllib.parse import urlsplit, urljoin

import pandas as pd
import requests
from PIL import Image
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import tempfile
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")

CONSENT_TEXTS = [
    "Cookies akzeptieren","ZUSTIMMEN UND WEITER","JA, KLINGT GUT.","Alle zulassen","Alle akzeptieren","Zustimmen","Akzeptieren",
    "Ablehnen","Alle ablehnen","Ohne Zustimmung fortfahren","Weiter ohne Einwilligung",
    "Continuer sans consentir","Continuer sans accepter","Rejeter","Tout refuser","Refuser tout",
    "Continue without consent","Continue without agreeing","Reject all","Reject All","J'accepte","Accepter",
    "OK","I accept","Agree","Accept all","Accept All",
]

EXCLUDE_URL_SUBSTRINGS = [
    "/wp-content/uploads/2017/10/steady.png",
    "/wp-content/uploads/2022/01/M2_Logo_01.jpg",
]
EXCLUDE_ALT_SUBSTRINGS = [
    "unterstütze uns auf paypal",
]

BLOCK_ANCESTOR_CLASSSETS = [
    {"teaser-absatz", "columns"},
]
BLOCK_PLAYERS_CLASSES = {
    "player-wrapper-2KhpeW", "bitmovinplayer-container", "jwplayer",
    "vjs-player", "brightcove", "video", "video-player",
}

from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import subprocess

_CHROMEDRIVER_PATH: str | None = None

def get_chromedriver_path() -> str:
    global _CHROMEDRIVER_PATH
    if _CHROMEDRIVER_PATH is None:
        _CHROMEDRIVER_PATH = ChromeDriverManager().install()
        subprocess.run(["chmod", "+x", _CHROMEDRIVER_PATH], capture_output=True)
    return _CHROMEDRIVER_PATH


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

    service = Service(get_chromedriver_path())
    return webdriver.Chrome(service=service, options=opts)
def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def get_ext(u: str) -> str:
    return os.path.splitext(urlsplit(u).path.lower())[1]

def ext_priority(u: str) -> int:
    e = get_ext(u)
    if e in (".jpg", ".jpeg"): return 4
    if e == ".png": return 3
    if e == ".webp": return 2
    if e == ".avif": return 1
    return 0

def infer_width_from_url(u: str) -> int:
    m = (re.search(r"/fit-in/(\d+)x", u)
         or re.search(r"[\W_](\d{3,4})w(?:[\W_]|$)", u)
         or re.search(r"/(\d{3,4})x\d{2,4}/", u)
         or re.search(r"/w(\d{3,4})(?=/|$)", u))
    return int(m.group(1)) if m else 0

def canonical_key(u: str) -> str:
    parts = urlsplit(u)
    path = parts.path
    path = re.sub(r"/t/[^/]+", "", path, flags=re.I)
    path = re.sub(r"/v\d+(?=/|$)", "", path, flags=re.I)
    path = re.sub(r"/w\d{2,4}(?=/|$)", "", path, flags=re.I)
    path = re.sub(r"/r\d+(?:\.\d+)?(?=/|$)", "", path, flags=re.I)
    path = re.sub(r"/{2,}", "/", path)
    path = re.sub(r"(/[^/]+?)-\d{2,4}x\d{2,4}(\.[a-z0-9]{2,4})$", r"\1\2", path, flags=re.I)
    return f"{parts.netloc}{path}".lower()

def safe_slug(s: str, n=64) -> str:
    s2 = re.sub(r"https?://", "", s or "")
    s2 = re.sub(r"[^\w.-]+", "_", s2).strip("._")
    return s2[-n:] if len(s2) > n else (s2 or "article")

def parse_srcset(srcset: str):
    out = []
    if not srcset:
        return out
    for part in [p.strip() for p in srcset.split(",") if p.strip()]:
        bits = part.split()
        u = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try: w = int(bits[1][:-1])
            except: w = 0
        out.append((u, w))
    return out


from selenium.webdriver.chrome.service import Service





def click_first(driver, by, sel) -> bool:
    try:
        els = driver.find_elements(by, sel)
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

def accept_consents(driver, timeout=15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        for t in CONSENT_TEXTS:
            xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
            if click_first(driver, By.XPATH, xp): return True
        for t in CONSENT_TEXTS:
            xp = f"//*[self::a or self::span][normalize-space()='{t}' or contains(., '{t}')]"
            if click_first(driver, By.XPATH, xp): return True
        for fr in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.frame(fr)
                for t in CONSENT_TEXTS:
                    xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
                    if click_first(driver, By.XPATH, xp):
                        driver.switch_to.default_content()
                        return True
                driver.switch_to.default_content()
            except:
                try: driver.switch_to.default_content()
                except: pass
        time.sleep(0.35)
    return False

def slow_scroll(driver, steps=26, dy=1600, pause=0.22):
    for _ in range(steps):
        ActionChains(driver).scroll_by_amount(0, dy).perform()
        time.sleep(pause)

def element_is_in_blocked_ancestor(tag) -> bool:
    for parent in tag.parents:
        if not getattr(parent, "attrs", None):
            continue
        classes = set(parent.get("class", []))
        if any(cs.issubset(classes) for cs in BLOCK_ANCESTOR_CLASSSETS):
            return True
        if parent.name == "article" and ("teaser" in classes or "teaser--embed" in classes):
            return True
        if classes & BLOCK_PLAYERS_CLASSES:
            return True
    return False

def url_or_alt_blacklisted(src: str, alt: str, title: str) -> bool:
    s = (src or "").lower()
    a = (alt or "").lower()
    t = (title or "").lower()
    if any(p in s for p in EXCLUDE_URL_SUBSTRINGS):
        return True
    if any(n in a or n in t for n in EXCLUDE_ALT_SUBSTRINGS):
        return True
    return False

def collect_images_from_article_body(html: str, base_url: str):
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one("#article_body_content.et_pb_row.et_pb_row_1_tb_body.hyphen")
    if not root:
        return []

    items = []

    imgs = list(root.select("figure.wp-block-image img"))
    imgs += [im for im in root.find_all("img") if im not in imgs]

    for im in imgs:
        try:
            if element_is_in_blocked_ancestor(im):
                continue

            src = (im.get("src") or "").strip()
            if not src or src.startswith(("data:", "blob:")):
                continue

            alt = im.get("alt") or ""
            title = im.get("title") or ""

            if url_or_alt_blacklisted(src, alt, title):
                continue

            src_abs = urljoin(base_url, src)

            caption = clean(title or alt or "")
            fig = im.find_parent("figure")
            if fig:
                cap_el = fig.find("figcaption") or fig.select_one(".wp-element-caption, .wp-caption-text")
                if cap_el and cap_el.get_text(strip=True):
                    caption = clean(cap_el.get_text(" ", strip=True))

            w = 0
            try:
                w = int(im.get("width") or "0")
            except:
                w = 0

            items.append({"url": src_abs, "w": w, "caption": caption})

            ss = im.get("srcset") or ""
            for u, ww in parse_srcset(ss):
                u_abs = urljoin(base_url, u)
                if url_or_alt_blacklisted(u_abs, alt, title):
                    continue
                items.append({"url": u_abs, "w": ww or infer_width_from_url(u_abs), "caption": caption})

        except:
            continue

    seen = set()
    uniq = []
    for c in items:
        u = c.get("url")
        if u and u not in seen:
            seen.add(u)
            uniq.append(c)
    return uniq

def score_candidate(c: dict) -> tuple[int, int]:
    w = int(c.get("w") or 0) or infer_width_from_url(c.get("url", ""))
    return (w, ext_priority(c.get("url", "")))

def choose_best_unique(items: list[dict]) -> list[dict]:
    best_by_asset = {}
    for c in items:
        u = c.get("url") or ""
        if not u:
            continue
        k = canonical_key(u)
        if (k not in best_by_asset) or (score_candidate(c) > score_candidate(best_by_asset[k])):
            best_by_asset[k] = c
    return list(best_by_asset.values())


def download_and_save(img_url: str, referer: str, out_dir: str, prefix: str) -> str | None:
    headers = {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }
    try:
        r = requests.get(img_url, headers=headers, timeout=60)
        if r.status_code != 200 or not r.content:
            return None

        ext = get_ext(img_url)
        ct = (r.headers.get("content-type") or "").lower()
        if not ext or ext not in IMG_EXT:
            if "jpeg" in ct:   ext = ".jpg"
            elif "png" in ct:  ext = ".png"
            elif "webp" in ct: ext = ".webp"
            elif "avif" in ct: ext = ".avif"
            else:              ext = ".jpg"

        h = hashlib.md5(img_url.encode("utf-8")).hexdigest()[:12]

        if ext == ".webp":
            fname = f"{prefix}_{h}.png"
            fpath = os.path.join(out_dir, fname)
            im = Image.open(BytesIO(r.content)).convert("RGBA")
            im.save(fpath, format="PNG")
            return fname
        else:
            fname = f"{prefix}_{h}{ext}"
            fpath = os.path.join(out_dir, fname)
            with open(fpath, "wb") as f:
                f.write(r.content)
            return fname
    except:
        return None

def screenshot_tweet_iframes(driver, article_url: str, out_dir: str, prefix: str):
    rows = []
    iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='platform.twitter.com/embed/Tweet.html']")
    for idx, fr in enumerate(iframes, 1):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", fr)
            time.sleep(0.5)
            tid = (fr.get_attribute("data-tweet-id") or "")
            if not tid:
                m = re.search(r"[?&]id=(\d+)", fr.get_attribute("src") or "")
                tid = m.group(1) if m else ""
            name = f"tweet_{tid or idx}_{hashlib.md5(f'{article_url}#tweet#{idx}'.encode('utf-8')).hexdigest()[:12]}.png"
            fr.screenshot(os.path.join(out_dir, name))
            rows.append({"image_url": "tweet", "caption": "", "path": name})
        except:
            continue
    return rows

def scrape_article_images_and_tweets(article_url: str, out_dir="wp_assets", headless=True) -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    rows: list[dict] = []

    driver = make_driver(headless=headless)
    try:
        driver.get(article_url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        accept_consents(driver, timeout=15)

        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#article_body_content.et_pb_row.et_pb_row_1_tb_body.hyphen")
                )
            )
        except:
            pass

        slow_scroll(driver, steps=26, dy=1600, pause=0.2)
        time.sleep(0.4)

        html = driver.page_source
        items = collect_images_from_article_body(html, base_url=article_url)
        dedup = choose_best_unique(items)

        prefix = safe_slug(article_url)

        for c in dedup:
            img = c.get("url")
            cap = c.get("caption", "")
            if not img: continue
            saved = download_and_save(img, referer=article_url, out_dir=out_dir, prefix=prefix)
            if saved:
                rows.append({"image_url": img, "caption": cap, "path": saved})

        rows.extend(screenshot_tweet_iframes(driver, article_url, out_dir, prefix))

    finally:
        driver.quit()

    return rows

def handle(review_url: str, location_info: str, headless: bool = True):
    os.makedirs(location_info, exist_ok=True)
    items = scrape_article_images_and_tweets(review_url, out_dir=location_info, headless=headless)

    if items:
        df = pd.DataFrame(items)
        if not df.empty:
            csv_path = os.path.join(location_info, "image_info.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8")

    return 0
