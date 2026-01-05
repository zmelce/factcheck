import os, re, json, hashlib
from urllib.parse import urlsplit
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

def extract_in_page(page):
    """Return [{image_url, caption}] but ONLY inside #block-factuel-content."""
    return page.evaluate("""
    () => {
      const ROOT = document.querySelector('#block-factuel-content') || document;
      const ALLOWED = ['.jpg','.jpeg','.png'];
      const clean = s => (s || '').replace(/\\s+/g,' ').trim();
      const ok = u => u && ALLOWED.some(e => (u.toLowerCase().split('?')[0] || '').endsWith(e));
      const abs = u => { try { return new URL(u, location.href).href } catch { return null } };
      const out = []; const seen = new Set();

      // Primary: blocks with wrapper-image
      const blocks = Array.from(ROOT.querySelectorAll('div.wrapper-image'));
      for (const b of blocks) {
        const cap = clean(Array.from(b.querySelectorAll('span.legend'))
                     .map(n=>n.textContent).join(' '));
        const imgs = Array.from(b.querySelectorAll('img[loading], img.img-fluid, img'));
        for (const img of imgs) {
          let u = img.currentSrc || img.src || img.getAttribute('data-src') || '';
          if (!u) {
            const ss = img.getAttribute('srcset') || img.getAttribute('data-srcset') || '';
            if (ss) {
              const parts = ss.split(',').map(s=>s.trim()).filter(Boolean);
              if (parts.length) u = parts[parts.length-1].split(/\\s+/)[0];
            }
          }
          const href = abs(u);
          if (!href || !ok(href) || seen.has(href)) continue;
          seen.add(href);
          out.push({image_url: href, caption: cap});
        }
      }

      // Fallback: any img exclusively INSIDE #block-factuel-content
      if (!out.length) {
        const imgs = Array.from(ROOT.querySelectorAll('img[loading], img.img-fluid, img'));
        for (const img of imgs) {
          let u = img.currentSrc || img.src || img.getAttribute('data-src') || '';
          if (!u) {
            const ss = img.getAttribute('srcset') || img.getAttribute('data-srcset') || '';
            if (ss) {
              const parts = ss.split(',').map(s=>s.trim()).filter(Boolean);
              if (parts.length) u = parts[parts.length-1].split(/\\s+/)[0];
            }
          }
          const href = abs(u);
          if (!href || !ok(href) || seen.has(href)) continue;

          let cap = '';
          const wrap = img.closest('div.wrapper-image');
          if (wrap && ROOT.contains(wrap)) {
            cap = clean(Array.from(wrap.querySelectorAll('span.legend'))
                       .map(n=>n.textContent).join(' '));
          }
          seen.add(href);
          out.push({image_url: href, caption: cap});
        }
      }
      return out;
    }
    """)

def scrape_with_playwright(url, out_dir,
                           headless=True, store_json=True):
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

        captured = {}  # url -> (content_type, bytes)
        def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                u = resp.url
                if "image/" in ct and u.split("?")[0].lower().endswith(ALLOWED_EXT):
                    try:
                        body = resp.body()
                        captured[u] = (ct, body)
                    except Exception:
                        pass
            except Exception:
                pass

        page = context.new_page()
        page.on("response", on_response)

        rec = {"reviewURL": url}

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            try:
                page.wait_for_selector("#block-factuel-content", timeout=20000)
            except Exception:
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
                except Exception:
                    pass

            for _ in range(8):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(350)
            page.wait_for_timeout(800)

            found = extract_in_page(page)

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
                if (saved != None):
                    image_name = os.path.basename(saved)
                images.append({"image_url": img, "caption": it["caption"], "path": image_name})

        except Exception as e:
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
    out_df =pd.DataFrame(out_list)
    if not out_df.empty:
        csv_path = os.path.join(location_info, "image_info.csv")
        out_df.to_csv(csv_path, index=False, encoding="utf-8")
    return 0
