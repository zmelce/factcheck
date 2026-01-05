import os, re, json, hashlib
from urllib.parse import urlsplit, urljoin
import pandas as pd
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ALLOWED_EXT  = (".jpg", ".jpeg", ".png", ".webp", ".avif")
ALLOWED_MIME = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/avif"}

def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def ext_of(u: str) -> str:
    return os.path.splitext(urlsplit(u).path.lower())[1]

def is_allowed(u: str) -> bool:
    return ext_of(u) in ALLOWED_EXT

def safe_slug(s: str, n=64):
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^\w.-]+", "_", s).strip("._")
    return s[-n:] if len(s) > n else (s or "article")


def extract_in_page(page, article_url: str) -> list[dict]:
    ALLOWED = (".jpg", ".jpeg", ".png")

    def ok(u: str) -> bool:
        if not u:
            return False
        return any(u.lower().split("?")[0].endswith(e) for e in ALLOWED)

    root = page.query_selector("#block-factuel-content") or page.query_selector("body")
    if not root:
        return []

    out = []
    seen = set()

    blocks = root.query_selector_all("div.wrapper-image")
    for b in blocks:
        legend_els = b.query_selector_all("span.legend")
        cap = clean(" ".join(el.inner_text() for el in legend_els))

        for img in b.query_selector_all("img[loading], img.img-fluid, img"):
            u = img.evaluate("el => el.currentSrc || ''") or img.get_attribute("src") or img.get_attribute("data-src") or ""
            if not u:
                srcset = img.get_attribute("srcset") or img.get_attribute("data-srcset") or ""
                parts = [p.strip() for p in srcset.split(",") if p.strip()]
                if parts:
                    u = parts[-1].split()[0]
            if not u:
                continue
            href = urljoin(article_url, u)
            if not ok(href) or href in seen:
                continue
            seen.add(href)
            out.append({"image_url": href, "caption": cap})

    if not out:
        for img in root.query_selector_all("img[loading], img.img-fluid, img"):
            u = img.evaluate("el => el.currentSrc || ''") or img.get_attribute("src") or img.get_attribute("data-src") or ""
            if not u:
                srcset = img.get_attribute("srcset") or img.get_attribute("data-srcset") or ""
                parts = [p.strip() for p in srcset.split(",") if p.strip()]
                if parts:
                    u = parts[-1].split()[0]
            if not u:
                continue
            href = urljoin(article_url, u)
            if not ok(href) or href in seen:
                continue

            cap = ""
            wrap_handle = img.evaluate_handle("el => el.closest('div.wrapper-image')")
            wrap_el = wrap_handle.as_element()
            if wrap_el:
                root_contains = root.evaluate("(root, el) => root.contains(el)", wrap_el)
                if root_contains:
                    legend_els = wrap_el.query_selector_all("span.legend")
                    cap = clean(" ".join(el.inner_text() for el in legend_els))

            seen.add(href)
            out.append({"image_url": href, "caption": cap})

    return out


def scrape_with_playwright(url, out_dir, headless=True, store_json=True):
    rows = []
    images = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox","--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="fr-FR",
            viewport={"width": 1366, "height": 900},
        )

        captured = {}
        def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                u = resp.url
                if "image/" in ct and u.split("?")[0].lower().endswith(ALLOWED_EXT):
                    try:
                        body = resp.body()
                        captured[u] = (ct, body)
                    except:
                        pass
            except:
                pass

        page = context.new_page()
        page.on("response", on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            try:
                page.wait_for_selector("#block-factuel-content", timeout=20000)
            except:
                pass

            for sel in [
                'button:has-text("J\'accepte")',
                'button:has-text("Accepter")',
                '#didomi-notice-agree-button',
            ]:
                try:
                    if page.locator(sel).count() > 0:
                        page.locator(sel).first.click(timeout=1500)
                        break
                except:
                    pass

            for _ in range(8):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(350)
            page.wait_for_timeout(800)

            found = extract_in_page(page, url)

            prefix = safe_slug(url)
            for it in found:
                img = it["image_url"]
                saved = None

                if img in captured:
                    ct, body = captured[img]
                    ext = ext_of(img) or (".jpg" if "jpeg" in ct else ".png")
                    h = hashlib.md5(img.encode("utf-8")).hexdigest()[:12]
                    path = os.path.join(out_dir, f"{h}{ext}")
                    with open(path, "wb") as f:
                        f.write(body)
                    saved = path

                if not saved:
                    base_noq = img.split("?", 1)[0]
                    match = next((u for u in captured if u.split("?",1)[0] == base_noq), None)
                    if match:
                        ct, body = captured[match]
                        ext = ext_of(match) or (".jpg" if "jpeg" in ct else ".png")
                        h = hashlib.md5(img.encode("utf-8")).hexdigest()[:12]
                        path = os.path.join(out_dir, f"{h}{ext}")
                        with open(path, "wb") as f:
                            f.write(body)
                        saved = path

                if not saved and is_allowed(img):
                    r = context.request.get(
                        img,
                        headers={
                            "Referer": url,
                            "User-Agent": UA,
                            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                        },
                        timeout=60000,
                    )
                    if r.ok:
                        ct = (r.headers.get("content-type") or "").lower()
                        if (ext_of(img) in ALLOWED_EXT) or (ct in ALLOWED_MIME):
                            ext = ext_of(img) or (".jpg" if "jpeg" in ct else ".png")
                            h = hashlib.md5(img.encode("utf-8")).hexdigest()[:12]
                            path = os.path.join(out_dir, f"{h}{ext}")
                            with open(path, "wb") as f:
                                f.write(r.body())
                            saved = path

                image_name = saved
                if saved is not None:
                    image_name = os.path.basename(saved)
                images.append({"image_url": img, "caption": it["caption"], "path": image_name})

        except:
            return []

        browser.close()
    return images

def handle(review_url: str, location_info: str):
    out_list = scrape_with_playwright(
        url=review_url,
        out_dir=location_info,
        headless=False,
        store_json=False,
    )
    out_df = pd.DataFrame(out_list)
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
