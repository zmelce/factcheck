
from __future__ import annotations

import os
import re
import sys
import json
import time
from html import unescape
from typing import List, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

CONSENT_TEXTS = [
    "Cookies akzeptieren","Alle akzeptieren","Alles akzeptieren","Zustimmen","Akzeptieren","Ich stimme zu",
    "Ohne Zustimmung fortfahren","Weiter ohne Einwilligung","Ablehnen","Alle ablehnen",
    "Accept all","Agree","I agree","Continue without consent","Reject all",
    "Continuer sans consentir","Tout accepter","Tout refuser",
]


def _make_driver(headless: bool = True) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,1200")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--lang=de-DE,de;q=0.9,en;q=0.8")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(f"--user-agent={UA}")
    opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    return webdriver.Chrome(options=opts)


def _click_first_button_with_text(driver, texts) -> bool:
    for t in texts:
        xp = f"//button[normalize-space()='{t}' or contains(., '{t}')]"
        els = driver.find_elements(By.XPATH, xp)
        if not els:
            continue
        for el in els:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(0.08)
                el.click()
                return True
            except Exception:
                continue
    return False


def _accept_consents(driver, timeout: int = 18) -> None:
    end = time.time() + timeout
    tried_iframes = False
    while time.time() < end:
        if _click_first_button_with_text(driver, CONSENT_TEXTS):
            return
        if not tried_iframes:
            for fr in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    driver.switch_to.frame(fr)
                    if _click_first_button_with_text(driver, CONSENT_TEXTS):
                        driver.switch_to.default_content()
                        return
                    driver.switch_to.default_content()
                except Exception:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
            tried_iframes = True
        time.sleep(0.25)


def _slow_scroll(driver, steps: int = 10, dy: int = 1400, pause: float = 0.18) -> None:
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, arguments[0]);", dy)
        time.sleep(pause)


def _wait_any_data_renditions(driver, timeout: int = 35) -> bool:
    js = """
      return Array.from(document.querySelectorAll('[data-renditions]'))
                  .some(n => (n.getAttribute('data-renditions')||'').length > 10);
    """
    try:
        WebDriverWait(driver, timeout).until(lambda d: d.execute_script(js) is True)
        return True
    except TimeoutException:
        return False


def _extract_progressive_js(driver) -> Optional[str]:
    js = r"""
    const nodes = document.querySelectorAll("[data-renditions]");
    for (const n of nodes) {
      const raw = n.getAttribute("data-renditions") || "";
      if (!raw) continue;
      const txt = (new DOMParser()).parseFromString(raw, "text/html").documentElement.textContent || "";
      try {
        const data = JSON.parse(txt);
        const url = data?.progressive?.url || "";
        if (url) return url;
      } catch(e) {}
    }
    return null;
    """
    try:
        return driver.execute_script(js)
    except Exception:
        return None


def _extract_progressive_regex(html: str) -> Optional[str]:
    for m in re.finditer(r'data-renditions="([^"]+)"', html, flags=re.I):
        raw = m.group(1)
        try:
            data = json.loads(unescape(raw))
            url = data.get("progressive", {}).get("url")  # <-- fixed: look under progressive.url
            if url:
                return url
        except Exception:
            continue
    return None


def _sniff_first_mp4(driver) -> Optional[str]:
    import json as _json
    for entry in driver.get_log("performance"):
        try:
            msg = _json.loads(entry.get("message", "{}"))
            params = msg.get("message", {}).get("params", {})
            for key in ("request", "response"):
                url = params.get(key, {}).get("url")
                if url and url.lower().endswith(".mp4"):
                    return url
        except Exception:
            continue
    return None


def handle(review_url: str, headless: bool = True) -> List[str]:
    driver = _make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        _accept_consents(driver, timeout=18)

        _slow_scroll(driver, steps=10, dy=1200, pause=0.18)
        driver.execute_script("window.scrollTo(0,0);")
        time.sleep(0.3)

        prog: Optional[str] = None
        if _wait_any_data_renditions(driver, timeout=35):
            prog = _extract_progressive_js(driver)

        if not prog:
            html = driver.page_source or ""
            prog = _extract_progressive_regex(html)

        if not prog:
            prog = _sniff_first_mp4(driver)

        return [prog] if prog else []
    finally:
        driver.quit()


