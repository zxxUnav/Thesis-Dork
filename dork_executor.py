import time
import random
import logging
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BLOCK_KEYWORDS = [
    "unusual traffic",
    "sorry",
    "detected unusual",
    "verify you are human",
    "captcha"
]


def setup_logger(log_path: str):
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    logging.info("Logger initialized")


def build_driver(browser="chrome", headless=True, wait=15):
    if browser == "chrome":
        opts = ChromeOptions()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        driver = webdriver.Chrome(options=opts)

    elif browser == "firefox":
        opts = FirefoxOptions()
        if headless:
            opts.add_argument("-headless")
        driver = webdriver.Firefox(options=opts)

    else:
        raise ValueError("Browser not supported")

    driver.set_page_load_timeout(wait)
    return driver


def is_blocked(page_source: str) -> bool:
    src = page_source.lower()
    return any(k in src for k in BLOCK_KEYWORDS)


def google_search(driver, query, max_results=10, wait=15):
    results = []

    driver.get("https://www.google.com")
    time.sleep(2)

    search_box = driver.find_element(By.NAME, "q")
    search_box.clear()
    search_box.send_keys(query)
    search_box.send_keys(Keys.RETURN)

    WebDriverWait(driver, wait).until(
        EC.presence_of_element_located((By.ID, "search"))
    )

    if is_blocked(driver.page_source):
        raise RuntimeError("Google blocked / CAPTCHA detected")

    blocks = driver.find_elements(By.CSS_SELECTOR, "div.g")
    rank = 1

    for b in blocks:
        if rank > max_results:
            break
        try:
            title = b.find_element(By.TAG_NAME, "h3").text
            url = b.find_element(By.TAG_NAME, "a").get_attribute("href")
            snippet = b.text
            results.append({
                "rank": rank,
                "title": title,
                "url": url,
                "snippet": snippet
            })
            rank += 1
        except Exception:
            continue

    return results


def save_block_screenshot(driver):
    Path("screenshots").mkdir(exist_ok=True)
    name = f"screenshots/blocked_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    driver.save_screenshot(name)
    return name
