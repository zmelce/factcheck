from __future__ import annotations

import os
import re
import time
import hashlib
import json
from html import unescape
from urllib.parse import urlsplit, parse_qs, urljoin

import requests
from bs4 import BeautifulSoup
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif")

CONSENT_TEXTS = [
    "Alle akzeptieren", "Alle Cookies akzeptieren",
    "Zustimmen", "Einverstanden", "Akzeptieren",
    "Ich stimme zu", "Ohne Zustimmung fortfahren",
    "Weiter ohne Einwilligung", "Ablehnen", "Alle ablehnen",
    "Accept all", "Agree", "Reject all",
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


def infer_width(u: str) -> int:
    qs = parse_qs(urlsplit(u).query)
    for key in ("width", "w"):
        if key in qs:
            try:
                return int(qs[key][0])
            except (ValueError, IndexError):
                pass
    return 0


def canonical_key(u: str) -> str:
    parts = urlsplit(u)
    path = parts.path
    _, fname = os.path.split(path)
    base = os.path.splitext(fname)[0] if fname else ""
    uuid_m = re.search(r"/image/([a-f0-9-]{36})/", path)
    if uuid_m:
        return f"{uuid_m.group(1)}:{base}"
    return f"{parts.netloc}:{base}" if base else f"{parts.netloc}{path}"


def ok_ext(u: str) -> bool:
    if not u:
        return False
    return any(urlsplit(u).path.lower().endswith(e) for e in IMG_EXT)


def safe_slug(s: str, n=64) -> str:
    s = re.sub(r"https?://", "", s or "")
    s = re.sub(r"[^\w.-]+", "_", s).strip("._")
    return s[-n:] if len(s) > n else (s or "article")



def make_driver(headless: bool = True):
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


def click_consent(driver, timeout: int = 18) -> None:
    end = time.time() + timeout
    tried_iframes = False
    while time.time() < end:
        for t in CONSENT_TEXTS:
            xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
            for el in driver.find_elements(By.XPATH, xp):
                try:
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", el
                    )
                    time.sleep(0.07)
                    el.click()
                    print(f"    Consent clicked: '{t}'")
                    return
                except:
                    continue
        if not tried_iframes:
            for fr in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    driver.switch_to.frame(fr)
                    for t in CONSENT_TEXTS:
                        xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
                        for el in driver.find_elements(By.XPATH, xp):
                            try:
                                el.click()
                                print(f"    Consent clicked (iframe): '{t}'")
                                driver.switch_to.default_content()
                                return
                            except:
                                continue
                    driver.switch_to.default_content()
                except:
                    try:
                        driver.switch_to.default_content()
                    except:
                        pass
            tried_iframes = True
        time.sleep(0.3)
    print("    Consent: no button found (timeout)")


def fetch_rendered_html(article_url: str, headless: bool = True) -> str | None:
    driver = make_driver(headless=headless)
    html = None
    try:
        print(f"    Selenium: loading {article_url}")
        driver.get(article_url)
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        click_consent(driver, timeout=18)
        time.sleep(1)

        from selenium.webdriver.common.action_chains import ActionChains
        _ac = ActionChains(driver)
        for _ in range(15):
            _ac.scroll_by_amount(0, 1400).perform()
            time.sleep(0.3)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        html = driver.page_source
        print(f"    Selenium: page_source length = {len(html)} chars")

        n_absatzbild = len(driver.find_elements(By.CSS_SELECTOR, "div.absatzbild__media"))
        n_ts_image = len(driver.find_elements(By.CSS_SELECTOR, "img.ts-image"))
        n_picture = len(driver.find_elements(By.CSS_SELECTOR, "picture.ts-picture"))
        n_source = len(driver.find_elements(By.CSS_SELECTOR, "picture source[srcset]"))
        print(f"    Selenium DOM: div.absatzbild__media={n_absatzbild}, "
              f"img.ts-image={n_ts_image}, picture.ts-picture={n_picture}, "
              f"source[srcset]={n_source}")

    except Exception as e:
        print(f"    Selenium ERROR: {e}")
    finally:
        driver.quit()

    return html



def parse_images_bs4(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    images = []

    article_el = soup.find("article")
    if not article_el:
        print(f"    [BS4] No <article> element found — skipping Strategy A")
        return images

    all_divs = article_el.find_all("div")
    absatzbild_divs = [
        div for div in all_divs
        if "absatzbild" in div.get("class", [])
    ]
    print(f"    [BS4] Found {len(absatzbild_divs)} div.absatzbild inside <article>")

    for ab_div in absatzbild_divs:
        if ab_div.find_parent(class_=re.compile(r"article-head")):
            continue
        if ab_div.find_parent(class_=re.compile(r"teaser-absatz")):
            continue
        if ab_div.find_parent(class_=re.compile(r"teaser-absatz")):
            continue
        if ab_div.find_parent("aside"):
            continue

        cap = ""
        copyright_text = ""
        info_p = ab_div.select_one(".absatzbild__info__text")
        if not info_p:
            info_p = ab_div.select_one(".absatzbild__info p")
        if info_p:
            cap = clean(info_p.get_text())

        media_div = ab_div.select_one(".absatzbild__media")
        if not media_div:
            continue

        img_el = media_div.select_one("img.ts-image") or media_div.select_one("img")
        if img_el:
            title_attr = img_el.get("title", "")
            if title_attr:
                parts = title_attr.split("|")
                if not cap:
                    cap = clean(parts[0])
                if len(parts) > 1:
                    copyright_text = clean(parts[1])
            if not cap:
                cap = clean(img_el.get("alt", ""))

        full_cap = f"{cap} © {copyright_text}" if copyright_text else cap

        for source in media_div.select("picture source[srcset]"):
            srcset = source.get("srcset", "").strip()
            for part in srcset.split(","):
                url = part.strip().split()[0] if part.strip() else ""
                if url and ok_ext(url):
                    images.append({"url": url, "w": infer_width(url), "cap": full_cap})

        if img_el:
            src = img_el.get("src", "").strip()
            if src and ok_ext(src):
                images.append({"url": src, "w": infer_width(src), "cap": full_cap})

    return images


def parse_images_all_ts(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    images = []

    article_el = soup.find("article")
    if not article_el:
        print(f"    [BS4-fallback] No <article> element found — skipping Strategy B")
        return images

    for img in article_el.select("img.ts-image"):
        absatzbild_parent = img.find_parent(
            "div", class_=lambda c: c and "absatzbild" in c and "absatzbild__" not in " ".join(c)
        )
        if not absatzbild_parent:
            continue
        if img.find_parent(class_=re.compile(r"article-head")):
            continue
        if img.find_parent(class_=re.compile(r"teaser-absatz")):
            continue
        if img.find_parent("aside"):
            continue
        src = img.get("src", "").strip()
        if not src or not ok_ext(src):
            continue
        cap = clean(img.get("title", "") or img.get("alt", ""))
        images.append({"url": src, "w": infer_width(src), "cap": cap})

    print(f"    [BS4-fallback] Found {len(images)} img.ts-image")
    return images


def parse_images_regex(html: str) -> list[dict]:
    pattern = re.compile(
        r'https?://images\.tagesschau\.de/image/'
        r'[a-f0-9-]{36}/[^\s"\'<>]+?'
        r'\.(?:jpg|jpeg|png|webp|avif)'
        r'(?:\?[^\s"\'<>]*)?',
        re.I,
    )
    urls = set()
    for m in pattern.finditer(html):
        raw = m.group(0)
        raw = raw.replace("&amp;", "&")
        urls.add(raw)

    images = []
    for u in sorted(urls):
        images.append({"url": u, "w": infer_width(u), "cap": ""})

    print(f"    [Regex] Found {len(images)} tagesschau CDN URLs")
    return images


def extract_all_images(html: str, base_url: str, driver=None) -> list[dict]:
    if driver:
        return parse_images_bs4(driver.page_source, base_url)

    return parse_images_bs4(html, base_url)



def extract_embeds(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    embeds = []
    seen = set()

    for iframe in soup.select("iframe"):
        src = iframe.get("src", "") or iframe.get("data-src", "")
        src = src.strip()
        if not src or src in seen:
            continue

        if "instagram.com" in src:
            m = re.search(r"instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)", src)
            url = f"https://www.instagram.com/p/{m.group(1)}/" if m else src
            if url not in seen:
                seen.add(url)
                embeds.append({"platform": "instagram", "url": url})
        elif "twitter.com" in src or "platform.x.com" in src:
            m = re.search(r"[?&]id=(\d+)", src)
            url = f"https://twitter.com/i/status/{m.group(1)}" if m else src
            if url not in seen:
                seen.add(url)
                embeds.append({"platform": "twitter", "url": url})
        elif "facebook.com" in src:
            if src not in seen:
                seen.add(src)
                embeds.append({"platform": "facebook", "url": src})
        elif "tiktok.com" in src:
            if src not in seen:
                seen.add(src)
                embeds.append({"platform": "tiktok", "url": src})
        elif "youtube.com/embed" in src:
            m = re.search(r"/embed/([a-zA-Z0-9_-]+)", src)
            if m:
                url = f"https://www.youtube.com/watch?v={m.group(1)}"
                if url not in seen:
                    seen.add(url)
                    embeds.append({"platform": "youtube", "url": url})

    for tw_m in re.finditer(r'https?://twitter\.com/\w+/status/(\d+)', html):
        url = tw_m.group(0)
        if url not in seen:
            seen.add(url)
            embeds.append({"platform": "twitter", "url": url})

    for v_div in soup.select("div.v-instance[data-v]"):
        data_v = v_div.get("data-v", "")
        if not data_v:
            continue
        data_v_clean = data_v.replace("&quot;", '"').replace("&amp;", "&")
        embed_url_m = re.search(r'"embed_url"\s*:\s*"([^"]+)"', data_v_clean)
        if not embed_url_m:
            continue
        embed_url = embed_url_m.group(1)
        if embed_url in seen:
            continue
        seen.add(embed_url)

        service_m = re.search(r'"service_name"\s*:\s*"([^"]+)"', data_v_clean)
        service_label_m = re.search(r'"service_label"\s*:\s*"([^"]+)"', data_v_clean)
        platform = service_m.group(1) if service_m else "external"
        label = service_label_m.group(1) if service_label_m else platform

        el_id = v_div.get("id", "")

        embeds.append({
            "platform": platform,
            "url": embed_url,
            "label": label,
            "el_id": el_id,
            "type": "v-instance",
        })

    return embeds



def screenshot_embeds(article_url: str, embeds: list[dict],
                      out_dir: str, prefix: str, headless: bool = True) -> list[dict]:
    if not embeds:
        return []

    rows = []
    driver = make_driver(headless=headless)
    try:
        driver.get(article_url)
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        click_consent(driver, timeout=18)
        time.sleep(1)

        from selenium.webdriver.common.action_chains import ActionChains
        _ac = ActionChains(driver)
        for _ in range(15):
            _ac.scroll_by_amount(0, 1400).perform()
            time.sleep(0.3)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        for i, emb in enumerate(embeds):
            platform = emb["platform"]
            embed_url = emb["url"]
            emb_type = emb.get("type", "")
            try:
                el = None

                if emb_type == "v-instance":
                    el_id = emb.get("el_id", "")
                    if el_id:
                        elements = driver.find_elements(By.ID, el_id)
                        if elements:
                            el = elements[0]
                    if not el:
                        elements = driver.find_elements(
                            By.CSS_SELECTOR, f"iframe[src*='{embed_url[:40]}']"
                        )
                        if elements:
                            try:
                                el = elements[0].find_element(By.XPATH, "ancestor::*[contains(concat(' ',@class,' '),' v-instance ')][1]")
                            except:
                                el = elements[0].find_element(By.XPATH, "..") if elements[0] else elements[0]
                            if not el:
                                el = elements[0]
                else:
                    if "twitter" in platform:
                        sel = "iframe[src*='twitter.com'], iframe[src*='platform.x.com']"
                    elif "instagram" in platform:
                        sel = "iframe[src*='instagram.com']"
                    elif "facebook" in platform:
                        sel = "iframe[src*='facebook.com']"
                    elif "tiktok" in platform:
                        sel = "iframe[src*='tiktok.com']"
                    elif "youtube" in platform:
                        sel = "iframe[src*='youtube.com/embed']"
                    else:
                        continue

                    elements = driver.find_elements(By.CSS_SELECTOR, sel)
                    if elements:
                        el = elements[i] if i < len(elements) else elements[0]

                if not el:
                    print(f"    Embed not found: {platform} ({embed_url[:60]})")
                    continue

                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                time.sleep(2)

                h = hashlib.md5(embed_url.encode("utf-8")).hexdigest()[:12]
                label = emb.get("label", platform)
                fname = f"{prefix}_{platform}_{h}.png"
                fpath = os.path.join(out_dir, fname)
                el.screenshot(fpath)

                if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                    caption = f"[{label} {embed_url}]"
                    rows.append({
                        "image_url": embed_url,
                        "caption": caption,
                        "path": fname,
                    })
                    print(f"    Embed screenshot OK: {fname}")
            except Exception as e:
                print(f"    Embed screenshot failed ({platform}): {e}")
    except Exception as e:
        print(f"    Embed session failed: {e}")
    finally:
        driver.quit()

    return rows



def scrape_article(article_url: str, out_dir: str, headless: bool = True):
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    prefix = safe_slug(article_url)

    driver = make_driver(headless=headless)
    try:
        print(f"    Loading page: {article_url}")
        driver.get(article_url)
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        click_consent(driver, timeout=18)
        time.sleep(1)

        from selenium.webdriver.common.action_chains import ActionChains
        _ac = ActionChains(driver)
        for _ in range(15):
            _ac.scroll_by_amount(0, 1400).perform()
            time.sleep(0.3)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        html = driver.page_source
        print(f"    Page loaded. Source: {len(html)} chars")

        candidates = parse_images_bs4(html, article_url)
        print(f"    Total image candidates: {len(candidates)}")

        if not candidates:
            print(f"    WARNING: No images found!")

        def score(c):
            return (c.get("w", 0), ext_priority(c["url"]))

        best = {}
        for c in candidates:
            key = canonical_key(c["url"])
            if key not in best or score(c) > score(best[key]):
                best[key] = c

        for c in candidates:
            key = canonical_key(c["url"])
            if key in best and not best[key].get("cap") and c.get("cap"):
                best[key]["cap"] = c["cap"]

        groups, no_cap = {}, []
        for c in best.values():
            ck = clean(c.get("cap", "")).lower()
            if ck:
                groups.setdefault(ck, []).append(c)
            else:
                no_cap.append(c)

        final = [max(lst, key=score) for lst in groups.values()] + no_cap
        print(f"    {len(final)} images after dedup")

        session = requests.Session()
        session.headers.update({
            "User-Agent": UA,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": article_url,
        })

        for c in final:
            img_url = c["url"]
            cap = c.get("cap", "")
            try:
                r = session.get(img_url, timeout=60)
                if r.status_code != 200:
                    print(f"    Download FAILED ({r.status_code}): {img_url[:80]}")
                    continue

                ct = (r.headers.get("content-type") or "").lower()
                ext = get_ext(img_url)
                if not ext or ext not in IMG_EXT:
                    if "jpeg" in ct:
                        ext = ".jpg"
                    elif "png" in ct:
                        ext = ".png"
                    elif "webp" in ct:
                        ext = ".webp"
                    elif "avif" in ct:
                        ext = ".avif"
                    else:
                        ext = ".jpg"

                h = hashlib.md5(img_url.encode("utf-8")).hexdigest()[:12]
                fname = f"{prefix}_{h}{ext}"
                fpath = os.path.join(out_dir, fname)
                with open(fpath, "wb") as f:
                    f.write(r.content)
                rows.append({"image_url": img_url, "caption": cap, "path": fname})
                print(f"    Image OK: {fname} ({len(r.content)} bytes)")
            except Exception as e:
                print(f"    Download error: {e}")

        embeds = extract_embeds(html)
        print(f"    Found {len(embeds)} embeds")

        if embeds:
            for i, emb in enumerate(embeds):
                platform = emb["platform"]
                embed_url = emb["url"]
                emb_type = emb.get("type", "")
                try:
                    el = None

                    if emb_type == "v-instance":
                        el_id = emb.get("el_id", "")
                        if el_id:
                            elements = driver.find_elements(By.ID, el_id)
                            if elements:
                                el = elements[0]
                        if not el:
                            elements = driver.find_elements(
                                By.CSS_SELECTOR,
                                f"iframe[src*='{embed_url[:40]}']",
                            )
                            if elements:
                                try:
                                    el = elements[0].find_element(By.XPATH, "ancestor::*[contains(concat(' ',@class,' '),' v-instance ')][1]")
                                except:
                                    el = elements[0].find_element(By.XPATH, "..") if elements[0] else elements[0]
                                if not el:
                                    el = elements[0]
                    else:
                        if "twitter" in platform:
                            sel = "iframe[src*='twitter.com'], iframe[src*='platform.x.com']"
                        elif "instagram" in platform:
                            sel = "iframe[src*='instagram.com']"
                        elif "facebook" in platform:
                            sel = "iframe[src*='facebook.com']"
                        elif "tiktok" in platform:
                            sel = "iframe[src*='tiktok.com']"
                        elif "youtube" in platform:
                            sel = "iframe[src*='youtube.com/embed']"
                        else:
                            continue
                        elements = driver.find_elements(By.CSS_SELECTOR, sel)
                        if elements:
                            el = elements[i] if i < len(elements) else elements[0]

                    if not el:
                        print(f"    Embed not found: {platform} ({embed_url[:60]})")
                        continue

                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", el
                    )
                    time.sleep(2)

                    h = hashlib.md5(embed_url.encode("utf-8")).hexdigest()[:12]
                    label = emb.get("label", platform)
                    fname = f"{prefix}_{platform}_{h}.png"
                    fpath = os.path.join(out_dir, fname)
                    el.screenshot(fpath)

                    if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
                        rows.append({
                            "image_url": embed_url,
                            "caption": f"[{label} {embed_url}]",
                            "path": fname,
                        })
                        print(f"    Embed screenshot OK: {fname}")
                except Exception as e:
                    print(f"    Embed screenshot failed ({platform}): {e}")

    except Exception as e:
        print(f"    FATAL: {e}")
    finally:
        driver.quit()

    return rows


def handle(review_url: str, location_info: str, headless: bool = True):
    os.makedirs(location_info, exist_ok=True)
    items = scrape_article(review_url, out_dir=location_info, headless=headless)

    if items:
        df = pd.DataFrame(items)
        if not df.empty:
            csv_path = os.path.join(location_info, "image_info.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0