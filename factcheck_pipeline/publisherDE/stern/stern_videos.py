
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


def make_driver(headless: bool = True) -> webdriver.Chrome:
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


def click_first_button_with_text(driver, texts) -> bool:
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
            except:
                continue
    return False


def accept_consents(driver, timeout: int = 18) -> None:
    end = time.time() + timeout
    tried_iframes = False
    while time.time() < end:
        if click_first_button_with_text(driver, CONSENT_TEXTS):
            return
        if not tried_iframes:
            for fr in driver.find_elements(By.TAG_NAME, "iframe"):
                try:
                    driver.switch_to.frame(fr)
                    if click_first_button_with_text(driver, CONSENT_TEXTS):
                        driver.switch_to.default_content()
                        return
                    driver.switch_to.default_content()
                except:
                    try:
                        driver.switch_to.default_content()
                    except:
                        pass
            tried_iframes = True
        time.sleep(0.25)


def slow_scroll(driver, steps: int = 10, dy: int = 1400, pause: float = 0.18) -> None:
    from selenium.webdriver.common.action_chains import ActionChains
    ac = ActionChains(driver)
    for _ in range(steps):
        ac.scroll_by_amount(0, dy).perform()
        time.sleep(pause)


def wait_any_data_renditions(driver, timeout: int = 35) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: any(
                len(el.get_attribute("data-renditions") or "") > 10
                for el in d.find_elements(By.CSS_SELECTOR, "[data-renditions]")
            )
        )
        return True
    except TimeoutException:
        return False


def extract_progressive_js(driver) -> Optional[str]:
    for el in driver.find_elements(By.CSS_SELECTOR, "[data-renditions]"):
        raw = el.get_attribute("data-renditions") or ""
        if not raw:
            continue
        try:
            data = json.loads(unescape(raw))
            url = data.get("progressive", {}).get("url", "")
            if url:
                return url
        except:
            continue
    return None


def extract_progressive_regex(html: str) -> Optional[str]:
    for m in re.finditer(r'data-renditions="([^"]+)"', html, flags=re.I):
        raw = m.group(1)
        try:
            data = json.loads(unescape(raw))
            url = data.get("progressive", {}).get("url")
            if url:
                return url
        except:
            continue
    return None


def sniff_first_mp4(driver) -> Optional[str]:
    import json as _json
    for entry in driver.get_log("performance"):
        try:
            msg = _json.loads(entry.get("message", "{}"))
            params = msg.get("message", {}).get("params", {})
            for key in ("request", "response"):
                url = params.get(key, {}).get("url")
                if url and url.lower().endswith(".mp4"):
                    return url
        except:
            continue
    return None


def handle(review_url: str, headless: bool = True) -> List[str]:
    driver = make_driver(headless=headless)
    try:
        driver.get(review_url)
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        accept_consents(driver, timeout=18)

        slow_scroll(driver, steps=10, dy=1200, pause=0.18)
        driver.execute_script("window.scrollTo(0,0);")
        time.sleep(0.3)

        prog: Optional[str] = None
        if wait_any_data_renditions(driver, timeout=35):
            prog = extract_progressive_js(driver)

        if not prog:
            html = driver.page_source or ""
            prog = extract_progressive_regex(html)

        if not prog:
            prog = sniff_first_mp4(driver)

        return [prog] if prog else []
    finally:
        driver.quit()


