"""Mobile-sized Chromium smoke tests against the real offline household web app.

Run separately from unit tests:
    pip install -e ".[web]" selenium
    python tests/mobile_ux_smoke.py

The checks require locally installed Chrome/Chromium and a compatible driver.
No remote server or market provider is accessed.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "mobile-ux"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def api(base: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


@contextmanager
def demo():
    with tempfile.TemporaryDirectory(prefix="mobile-ux-household-") as folder:
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        log_path = ARTIFACTS / f"server-{port}.log"
        with log_path.open("wb") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "examples" / "m11_local_web.py"),
                    "--serve", "--host", "127.0.0.1",
                    "--port", str(port), "--data-dir", folder,
                ],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError(f"Demo server stopped; see {log_path}")
                    try:
                        api(base, "/catalog")
                        break
                    except (URLError, TimeoutError, ConnectionError):
                        time.sleep(0.1)
                else:
                    raise RuntimeError(f"Demo server did not start; see {log_path}")
                yield base
            finally:
                server.terminate()
                try:
                    server.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=6)


@contextmanager
def mobile_browser():
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_experimental_option(
        "mobileEmulation",
        {
            "deviceMetrics": {
                "width": 390,
                "height": 844,
                "pixelRatio": 2,
                "touch": True,
            }
        },
    )
    browser = webdriver.Chrome(options=opts)
    browser.set_page_load_timeout(35)
    try:
        yield browser
    finally:
        browser.quit()


def prepare_home(browser, base: str) -> None:
    browser.get(base + "/")
    wait = WebDriverWait(browser, 20)
    wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "#connection-status.online"))
    layer = browser.find_element(By.ID, "onboarding-layer")
    if "hidden" not in layer.get_attribute("class").split():
        browser.find_element(By.ID, "onboarding-skip").click()
    browser.find_element(By.CSS_SELECTOR, '[data-view="home"]').click()
    wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "#home-items .home-card"))


def cards(browser) -> list:
    return browser.find_elements(By.CSS_SELECTOR, "#home-items .home-card")


def item_card(browser, text: str):
    return next(card for card in cards(browser) if text in card.text)


def select_stock(browser, item: str, choice: str) -> None:
    card = item_card(browser, item)
    buttons = card.find_elements(By.CSS_SELECTOR, ".stock-choice")
    next(button for button in buttons if button.text == choice).click()


def viewport_check(browser, label: str) -> None:
    metrics = browser.execute_script(
        """return {
          viewport: window.innerWidth,
          document: document.documentElement.scrollWidth,
          body: document.body.scrollWidth,
          width: document.querySelector('#view-home').getBoundingClientRect().width
        };"""
    )
    assert metrics["viewport"] == 390, (label, metrics)
    assert metrics["document"] <= metrics["viewport"] + 1, (label, metrics)
    assert metrics["body"] <= metrics["viewport"] + 1, (label, metrics)


def screenshot(browser, name: str) -> None:
    path = ARTIFACTS / f"{name}.png"
    browser.save_screenshot(str(path))
    print(f"Screenshot: {path}", flush=True)


def first_batch(browser) -> None:
    with demo() as base:
        prepare_home(browser, base)
        wait = WebDriverWait(browser, 20)
        assert len(cards(browser)) == 3, "Empty household must show full catalog"
        viewport_check(browser, "initial")
        screenshot(browser, "01-empty-household")

        select_stock(browser, "Молоко", "1")
        assert len(cards(browser)) == 3, "First pending stocktake must not collapse catalog"
        assert "Изменено товаров: 1" in browser.find_element(
            By.ID, "stocktake-pending-label"
        ).text

        select_stock(browser, "Рис", "½")
        assert len(cards(browser)) == 3, "Batch must preserve access to remaining items"
        assert "Изменено товаров: 2" in browser.find_element(
            By.ID, "stocktake-pending-label"
        ).text
        screenshot(browser, "02-two-pending")
        browser.find_element(By.ID, "save-pending-stocktakes").click()
        wait.until(lambda d: len(cards(d)) == 2)
        wait.until(lambda d: "hidden" in d.find_element(
            By.ID, "stocktake-actions"
        ).get_attribute("class").split())
        assert api(base, "/household/history")["event_count"] == 2
        viewport_check(browser, "saved two items")
        screenshot(browser, "03-focused-after-save")

        browser.refresh()
        wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "#connection-status.online"))
        browser.find_element(By.CSS_SELECTOR, '[data-view="home"]').click()
        wait.until(lambda d: len(cards(d)) == 2)
        assert "Молоко" in browser.find_element(By.ID, "home-items").text
        assert "Рис" in browser.find_element(By.ID, "home-items").text
        assert "Масло" not in browser.find_element(By.ID, "home-items").text
        browser.find_element(By.ID, "home-filter-toggle").click()
        assert len(cards(browser)) == 3, "All items must remain one tap away"
        browser.find_element(By.ID, "home-filter-toggle").click()
        assert len(cards(browser)) == 2
        viewport_check(browser, "focused reopened")
        screenshot(browser, "04-focused-after-reopen")
        print("PASS first batch: 2 sequential updates, one save, reopen, toggle", flush=True)


def reminder_reentry(browser) -> None:
    with demo() as base:
        now = datetime.now(timezone.utc)
        for item, amount, unit, days in (
            ("milk", "1", "l", 16),
            ("rice", "0.5", "kg", 2),
        ):
            api(base, "/household/stocktakes", {
                "event_id": f"mobile-ux-check-{item}",
                "item_id": item,
                "quantity": {"amount": amount, "unit": unit},
                "occurred_at": (now - timedelta(days=days)).isoformat(),
                "reason": "mobile UX smoke fixture",
            })
        api(base, "/household/purchases", {
            "event_id": "mobile-ux-oil-purchase-only",
            "sku_id": "oil-1l",
            "packs": 1,
        })
        assert api(base, "/household/history")["event_count"] == 3
        prepare_home(browser, base)
        viewport_check(browser, "reminder reentry")

        milk = item_card(browser, "Молоко").text
        rice = item_card(browser, "Рис").text
        oil = item_card(browser, "Масло").text
        assert "Проверяли 16 дн. назад" in milk, milk
        assert "Стоит уточнить" not in rice, rice
        assert "Ещё не сверяли остаток" in oil, oil
        assert "Стоит проверить: 2" in browser.find_element(
            By.ID, "home-check-summary"
        ).text
        screenshot(browser, "05-reminders-on-reentry")
        assert api(base, "/household/history")["event_count"] == 3, (
            "Rendering hints must not write facts"
        )

        select_stock(browser, "Масло", "1")
        assert "Ещё не сверяли остаток" not in item_card(browser, "Масло").text
        assert "Стоит проверить: 1" in browser.find_element(
            By.ID, "home-check-summary"
        ).text
        assert api(base, "/household/history")["event_count"] == 3, (
            "Pending selection must not write facts"
        )
        screenshot(browser, "06-hint-dismissed-after-selection")
        print("PASS reminder reentry: old/recent/purchase-only and no silent writes", flush=True)


def onboarding_wizard(browser) -> None:
    """Complete the 3-step mobile onboarding with two stock observations."""
    with demo() as base:
        browser.get(base + "/")
        wait = WebDriverWait(browser, 20)
        wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "#connection-status.online"))
        layer = browser.find_element(By.ID, "onboarding-layer")
        wait.until(lambda d: layer.is_displayed())
        assert "Добро пожаловать" in browser.find_element(By.ID, "onboarding-content").text

        def visible_wizard() -> None:
            bounds = browser.execute_script(
                """const b = document.querySelector('.onboarding-card').getBoundingClientRect();
                   return {left:b.left,right:b.right,width:window.innerWidth};"""
            )
            assert bounds["left"] >= -1 and bounds["right"] <= bounds["width"] + 1, bounds

        visible_wizard()
        screenshot(browser, "07-onboarding-welcome")
        browser.find_element(By.CSS_SELECTOR, "#onboarding-actions .onboarding-primary").click()
        wait.until(lambda d: "Что у вас обычно бывает дома?" in d.find_element(
            By.ID, "onboarding-content"
        ).text)

        for name in ("Молоко", "Рис"):
            options = browser.find_elements(By.CSS_SELECTOR, "#onboarding-content .onboarding-product")
            next(x for x in options if name in x.text).click()
        assert "Выбрано: 2" in browser.find_element(By.ID, "onboarding-content").text
        visible_wizard()
        screenshot(browser, "08-onboarding-two-products")
        browser.find_element(By.CSS_SELECTOR, "#onboarding-actions .onboarding-primary").click()
        wait.until(lambda d: "Сколько сейчас есть?" in d.find_element(
            By.ID, "onboarding-content"
        ).text)

        for name, label in (("Молоко", "1 упаковка"), ("Рис", "Нет")):
            rows = browser.find_elements(By.CSS_SELECTOR, "#onboarding-content .onboarding-stock-row")
            row = next(x for x in rows if name in x.text)
            next(x for x in row.find_elements(
                By.CSS_SELECTOR, ".onboarding-stock-choice"
            ) if x.text == label).click()
        visible_wizard()
        screenshot(browser, "09-onboarding-stock-selections")
        browser.find_element(By.CSS_SELECTOR, "#onboarding-actions .onboarding-primary").click()
        wait.until(lambda d: "Можно начинать" in d.find_element(
            By.ID, "onboarding-content"
        ).text)
        assert api(base, "/household/history")["event_count"] == 2
        balances = {
            x["item_id"]: x["quantity"]
            for x in api(base, "/household/state")["household"]["balances"]
        }
        assert balances["milk"] == {"amount": "1", "unit": "l"}, balances
        assert balances["rice"] == {"amount": "0", "unit": "kg"}, balances
        assert "Рис" in browser.find_element(By.ID, "onboarding-content").text

        actions = browser.find_elements(By.CSS_SELECTOR, "#onboarding-actions button")
        next(x for x in actions if x.text == "Посмотреть запасы").click()
        wait.until(lambda d: len(cards(d)) == 2)
        viewport_check(browser, "onboarding finished")
        screenshot(browser, "10-onboarding-done-home")
        print("PASS onboarding wizard: 3 steps, 2 facts, missing item, mobile width", flush=True)


def main() -> None:
    with mobile_browser() as browser:
        onboarding_wizard(browser)
        first_batch(browser)
        reminder_reentry(browser)
    print("MOBILE_UX_SMOKE_OK", flush=True)


if __name__ == "__main__":
    main()
