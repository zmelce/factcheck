
import os
import re
import time
import hashlib
from io import BytesIO
from urllib.parse import urlsplit

import requests
import pandas as pd
from PIL import Image

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")

CONSENT_TEXTS = [
    "ZUSTIMMEN UND WEITER", "JA, KLINGT GUT.", "Alle zulassen", "Alle akzeptieren", "Zustimmen", "Akzeptieren",
    "Ablehnen", "Alle ablehnen", "Ohne Zustimmung fortfahren", "Weiter ohne Einwilligung",
    "Continuer sans consentir", "Continuer sans accepter", "Rejeter", "Tout refuser", "Refuser tout",
    "Continue without consent", "Continue without agreeing", "Reject all", "Reject All", "J'accepte", "Accepter",
    "OK", "I accept", "Agree", "Accept all", "Accept All",
]


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
    m = (
        re.search(r"/fit-in/(\d+)x", u)
        or re.search(r"[\W_](\d{3,4})w(?:[\W_]|$)", u)
        or re.search(r"/(\d{3,4})x\d{2,4}/", u)
        or re.search(r"/w(\d{3,4})(?=/|$)", u)
    )
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

def caption_key(s: str) -> str:
    return clean(s).lower()

def safe_slug(s: str, n=64) -> str:
    s2 = re.sub(r"https?://", "", s or "")
    s2 = re.sub(r"[^\w.-]+", "_", s2).strip("._")
    return s2[-n:] if len(s2) > n else (s2 or "article")

def parse_srcset(srcset: str) -> list[tuple[str, int]]:
    out = []
    if not srcset:
        return out
    for part in [p.strip() for p in srcset.split(",") if p.strip()]:
        bits = part.split()
        url = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try: w = int(bits[1][:-1])
            except: w = 0
        out.append((url, w))
    return out


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
            except Exception:
                continue
    except Exception:
        pass
    return False

def accept_consents(driver, timeout=15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        for t in CONSENT_TEXTS:
            xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
            if click_first(driver, By.XPATH, xp):
                return True
        for t in CONSENT_TEXTS:
            xp = f"//*[self::a or self::span][normalize-space()='{t}' or contains(., '{t}')]"
            if click_first(driver, By.XPATH, xp):
                return True
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for fr in frames:
            try:
                driver.switch_to.frame(fr)
                for t in CONSENT_TEXTS:
                    xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
                    if click_first(driver, By.XPATH, xp):
                        driver.switch_to.default_content()
                        return True
                driver.switch_to.default_content()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
        time.sleep(0.35)
    return False

def slow_scroll(driver, steps=22, dy=1500, pause=0.22):
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
        time.sleep(pause)


def element_has_ancestor_css(driver, el, css: str) -> bool:
    try:
        return driver.execute_script(
            "const el = arguments[0], sel = arguments[1]; return !!(el && el.closest(sel));",
            el, css
        ) or False
    except Exception:
        return False

def is_excluded_gps_widget_image(img_el) -> bool:
    try:
        src = (img_el.get_attribute("src") or "").lower()
        srcset = (img_el.get_attribute("srcset") or "").lower()
        title = (img_el.get_attribute("title") or "").lower()
        alt = (img_el.get_attribute("alt") or "").lower()
        if "gps-visual-widget" in src or "gps-visual-widget" in srcset:
            return True
        needle = "gregor peter schmitz with the letters gps"
        if needle in title or needle in alt:
            return True
    except Exception:
        pass
    return False

def is_inside_teaser_embed(driver, el) -> bool:
    try:
        return driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return false;
            return !!el.closest(
              'article.teaser.teaser--embed, article.teaser--embed, article.teaser[data-teaser-type="embed"], article[data-teaser-type="embed"], article[data-testid="paid-teaser"]'
            );
            """,
            el
        ) or False
    except Exception:
        return False

def find_article_root(driver):
    arts = driver.find_elements(By.CSS_SELECTOR, "article.article")
    if arts:
        return arts[0]
    arts = driver.find_elements(By.TAG_NAME, "article")
    return arts[0] if arts else driver.find_element(By.TAG_NAME, "body")

def extract_images_and_captions(driver) -> list[dict]:
    items = []
    article = find_article_root(driver)
    imgs = article.find_elements(By.CSS_SELECTOR, "img.ts-image, img.image, figure img, picture img, img")

    for im in imgs:
        try:
            if element_has_ancestor_css(driver, im, ".teaser-absatz.columns"):
                continue
            if is_inside_teaser_embed(driver, im):
                continue
            if is_excluded_gps_widget_image(im):
                continue

            src = (im.get_attribute("src") or "").strip()
            title = (im.get_attribute("title") or "").strip()
            alt = (im.get_attribute("alt") or "").strip()
            cap = title or alt

            try:
                cap2 = driver.execute_script(
                    """
                    const im = arguments[0];
                    const fig = im.closest('figure');
                    if (fig) {
                      const fc = fig.querySelector('figcaption');
                      if (fc) return fc.textContent.trim();
                    }
                    const legend = im.closest('.wrapper-image')?.querySelector('.legend');
                    return legend ? legend.textContent.trim() : '';
                    """,
                    im
                ) or ""
                if cap2:
                    cap = clean(cap2)
            except Exception:
                pass

            candidates = []
            if src:
                try: w = int((im.get_attribute("width") or "0").strip())
                except: w = 0
                if not w: w = infer_width_from_url(src)
                candidates.append({"url": src, "w": w, "caption": cap})

            srcset = (im.get_attribute("srcset") or "").strip()
            if srcset:
                for u, w in parse_srcset(srcset):
                    if not u: continue
                    if "gps-visual-widget" in u:
                        continue
                    candidates.append({"url": u, "w": w or infer_width_from_url(u), "caption": cap})

            seen = set()
            for c in candidates:
                u = c["url"]
                if not u or u in seen: continue
                seen.add(u)
                items.append(c)
        except Exception:
            continue
    return items

def extract_tweet_iframes(driver):
    return driver.find_elements(By.CSS_SELECTOR, "iframe[src*='platform.twitter.com/embed/Tweet.html']")

def score_candidate(c: dict) -> tuple[int, int]:
    w = int(c.get("w") or 0) or infer_width_from_url(c.get("url", ""))
    return (w, ext_priority(c.get("url", "")))

def choose_best_unique(items: list[dict]) -> list[dict]:
    best_by_asset = {}
    for c in items:
        u = c.get("url") or ""
        if not u: continue
        k = canonical_key(u)
        if (k not in best_by_asset) or (score_candidate(c) > score_candidate(best_by_asset[k])):
            best_by_asset[k] = c

    groups = {}
    for c in best_by_asset.values():
        capk = caption_key(c.get("caption", ""))
        groups.setdefault(capk, []).append(c)

    final_list = []
    for lst in groups.values():
        final_list.append(max(lst, key=score_candidate))
    return final_list


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
            if "jpeg" in ct: ext = ".jpg"
            elif "png" in ct: ext = ".png"
            elif "webp" in ct: ext = ".webp"
            elif "avif" in ct: ext = ".avif"
            else: ext = ".jpg"

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
    except Exception:
        return None

def screenshot_iframe(driver, iframe_el, article_url: str, out_dir: str, prefix: str, idx: int) -> str | None:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", iframe_el)
        time.sleep(0.6)
        tid = (iframe_el.get_attribute("data-tweet-id") or "").strip()
        if not tid:
            src = (iframe_el.get_attribute("src") or "")
            m = re.search(r"[?&]id=(\d+)", src)
            tid = m.group(1) if m else ""
        name = f"tweet_{tid or idx}_{hashlib.md5(f'{article_url}#tweet#{idx}'.encode('utf-8')).hexdigest()[:12]}.png"
        path = os.path.join(out_dir, name)
        iframe_el.screenshot(path)
        return name
    except Exception:
        return None


def scrape_article_images_and_tweets(article_url: str, out_dir="stern_assets", headless=True) -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    rows: list[dict] = []

    driver = make_driver(headless=headless)
    try:
        driver.get(article_url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        accept_consents(driver, timeout=18)

        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.sub-header.informations"))
            )
        except Exception:
            pass

        slow_scroll(driver, steps=22, dy=1600, pause=0.2)
        time.sleep(0.5)

        items = extract_images_and_captions(driver)
        dedup = choose_best_unique(items)
        prefix = safe_slug(article_url)

        for c in dedup:
            img = c.get("url")
            cap = c.get("caption", "")
            if not img: continue
            saved = download_and_save(img, referer=article_url, out_dir=out_dir, prefix=prefix)
            if saved:
                rows.append({"image_url": img, "caption": cap, "path": saved})

        iframes = extract_tweet_iframes(driver)
        for i, fr in enumerate(iframes, 1):
            name = screenshot_iframe(driver, fr, article_url, out_dir, prefix, i)
            if name:
                rows.append({"image_url": "tweet", "caption": "", "path": name})

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
