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
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
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
        assert balances["milk"]["unit"] == "ml", balances
        assert Decimal(balances["milk"]["amount"]) == Decimal("1000"), balances
        assert balances["rice"]["unit"] == "g", balances
        assert Decimal(balances["rice"]["amount"]) == Decimal("0"), balances
        assert "Рис" in browser.find_element(By.ID, "onboarding-content").text

        actions = browser.find_elements(By.CSS_SELECTOR, "#onboarding-actions button")
        next(x for x in actions if x.text == "Посмотреть запасы").click()
        wait.until(lambda d: len(cards(d)) == 2)
        viewport_check(browser, "onboarding finished")
        screenshot(browser, "10-onboarding-done-home")
        print("PASS onboarding wizard: 3 steps, 2 facts, missing item, mobile width", flush=True)


def routine_stand_preview(browser) -> None:
    """A real browser can save a routine and preview without making household facts."""
    with demo() as base:
        browser.get(base + "/")
        wait = WebDriverWait(browser, 20)
        wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "#connection-status.online"))
        onboarding = browser.find_element(By.ID, "onboarding-layer")
        if onboarding.is_displayed():
            browser.find_element(By.ID, "onboarding-skip").click()
        panel = browser.find_element(By.ID, "usual-basket-panel")
        assert panel.is_displayed(), "Demo must expose the routine stand"
        panel.find_element(By.CSS_SELECTOR, "summary").click()
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "#usual-basket-list .usual-basket-item"))
        screenshot(browser, "11-usual-basket-empty")
        rows = browser.find_elements(By.CSS_SELECTOR, "#usual-basket-list .usual-basket-item")
        milk = next(x for x in rows if "Молоко" in x.text)
        milk.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]').click()

        rows = browser.find_elements(By.CSS_SELECTOR, "#usual-basket-list .usual-basket-item")
        milk = next(x for x in rows if "Молоко" in x.text)
        Select(milk.find_element(
            By.CSS_SELECTOR, 'select[aria-label^="Резервный объём"]'
        )).select_by_value("one")
        assert not browser.find_element(By.ID, "save-usual-basket").get_attribute("disabled")
        save = browser.find_element(By.ID, "save-usual-basket")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});", save
        )
        wait.until(lambda d: d.execute_script(
            """const b=arguments[0].getBoundingClientRect();
               const x=b.left+b.width/2, y=b.top+b.height/2;
               return y<window.innerHeight-90 &&
                 document.elementFromPoint(x,y)===arguments[0];""", save
        ))
        save.click()
        wait.until(lambda d: "Сохранено привычных товаров: 1" in d.find_element(
            By.ID, "usual-basket-status"
        ).text)
        assert api(base, "/household/usual-basket")["usual_basket"]["items"][0]["item_id"] == "milk"
        assert api(base, "/household/history")["event_count"] == 0

        budget = browser.find_element(By.ID, "plan-budget")
        budget.clear()
        budget.send_keys("500")
        preview_button = browser.find_element(By.ID, "preview-usual-basket")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});", preview_button
        )
        wait.until(lambda d: d.execute_script(
            """const b=arguments[0].getBoundingClientRect();
               const x=b.left+b.width/2, y=b.top+b.height/2;
               return y<window.innerHeight-90 &&
                 document.elementFromPoint(x,y)===arguments[0];""", preview_button
        ))
        # Prove the browser discards an in-flight preview if its inputs change.
        browser.execute_script("""
            window.__originalRoutineFetch = window.fetch.bind(window);
            window.__releaseRoutinePreview = null;
            window.fetch = (...args) => {
              const running = window.__originalRoutineFetch(...args);
              if (args[0] === "/household/usual-basket/preview") {
                return new Promise(resolve => {
                  window.__releaseRoutinePreview = () => running.then(resolve);
                });
              }
              return running;
            };
        """)
        preview_button.click()
        wait.until(lambda d: d.execute_script(
            "return typeof window.__releaseRoutinePreview === 'function';"
        ))
        budget.clear()
        budget.send_keys("550")
        browser.execute_script("window.__releaseRoutinePreview();")
        wait.until(lambda d: not d.find_element(
            By.ID, "preview-usual-basket"
        ).get_attribute("disabled"))
        assert not browser.find_elements(By.ID, "confirm-usual-basket")
        assert not browser.find_element(By.ID, "usual-basket-preview").is_displayed()
        browser.execute_script("window.fetch = window.__originalRoutineFetch;")
        budget.clear()
        budget.send_keys("500")
        preview_button = browser.find_element(By.ID, "preview-usual-basket")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
            preview_button,
        )
        wait.until(lambda d: d.execute_script(
            """const b=arguments[0].getBoundingClientRect();
               const x=b.left+b.width/2, y=b.top+b.height/2;
               return y<window.innerHeight-90 &&
                 document.elementFromPoint(x,y)===arguments[0];""", preview_button
        ))
        preview_button.click()
        wait.until(lambda d: "Ожидаемые расходы" in d.find_element(
            By.ID, "usual-basket-preview"
        ).text)
        assert "по указанному количеству" in browser.find_element(
            By.ID, "usual-basket-preview"
        ).text
        viewport_check(browser, "routine preview")
        screenshot(browser, "12-usual-basket-preview")
        assert api(base, "/household/history")["event_count"] == 0
        assert len(api(base, "/plans?limit=12")["plans"]) == 0

        confirmation = browser.find_element(By.ID, "confirm-usual-basket")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
            confirmation,
        )
        wait.until(lambda d: d.execute_script(
            """const b=arguments[0].getBoundingClientRect();
               const x=b.left+b.width/2, y=b.top+b.height/2;
               return y<window.innerHeight-90 &&
                 document.elementFromPoint(x,y)===arguments[0];""", confirmation
        ))
        confirmation.click()
        wait.until(lambda d: "Список сохранён" in d.find_element(
            By.ID, "usual-basket-preview"
        ).text)
        assert len(api(base, "/plans?limit=12")["plans"]) == 1
        assert api(base, "/household/history")["event_count"] == 0
        assert browser.find_element(By.ID, "plan-result-panel").is_displayed()
        viewport_check(browser, "routine saved plan")
        screenshot(browser, "14-usual-basket-confirmed-no-purchase")

        browser.refresh()
        wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "#connection-status.online"))
        onboarding = browser.find_element(By.ID, "onboarding-layer")
        if onboarding.is_displayed():
            browser.find_element(By.ID, "onboarding-skip").click()
        summary = browser.find_element(By.CSS_SELECTOR, "#usual-basket-panel summary")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
            summary,
        )
        wait.until(lambda d: d.execute_script(
            """const b=arguments[0].getBoundingClientRect();
               const x=b.left+b.width/2, y=b.top+b.height/2;
               return y>=0 && y<window.innerHeight-90 &&
                 document.elementFromPoint(x,y)===arguments[0];""", summary
        ))
        summary.click()
        wait.until(lambda d: "Сохранено привычных товаров: 1" in d.find_element(
            By.ID, "usual-basket-status"
        ).text)
        screenshot(browser, "13-usual-basket-reloaded")
        assert len(api(base, "/plans?limit=12")["plans"]) == 1
        assert api(base, "/household/history")["event_count"] == 0

        # The next shop should reuse budget/horizon, but NOT replay the old stock.
        settings = api(base, "/household/usual-basket/last-settings")["repeat_settings"]
        assert settings["budget"] == {"amount": "500", "currency": "KGS"}
        assert settings["horizon_days"] == "7"
        assert settings["source_plan_id"] == api(base, "/plans?limit=12")["plans"][0]["plan_id"]
        corrected = api(base, "/household/stocktakes", {
            "event_id": "repeat-updated-milk-stock",
            "item_id": "milk",
            "quantity": {"amount": "2", "unit": "l"},
            "reason": "updated before second weekly shop",
        })
        assert corrected["event"]["event_id"] == "repeat-updated-milk-stock"
        budget = browser.find_element(By.ID, "plan-budget")
        budget.clear()
        budget.send_keys("900")
        # Stored Decimal horizons must be accepted exactly, never rounded.
        assert browser.execute_script(
            "return [parseConfirmedHorizonDays('7.0'), "
            "parseConfirmedHorizonDays('1E+1'), "
            "parseConfirmedHorizonDays('7.5')];"
        ) == [7, 10, None]

        repeat = browser.find_element(By.ID, "repeat-usual-basket")
        wait.until(lambda d: repeat.is_displayed() and repeat.is_enabled())
        assert "500 KGS" in repeat.text
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
            repeat,
        )
        wait.until(lambda d: d.execute_script(
            """const b=arguments[0].getBoundingClientRect();
               const x=b.left+b.width/2, y=b.top+b.height/2;
               return y>=0 && y<window.innerHeight-90 &&
                 document.elementFromPoint(x,y)===arguments[0];""", repeat
        ))
        repeat.click()
        wait.until(lambda d: "Ожидаемые расходы" in d.find_element(
            By.ID, "usual-basket-preview"
        ).text)
        assert browser.find_element(By.ID, "plan-budget").get_attribute("value") == "500"
        assert browser.find_element(By.ID, "plan-horizon").get_attribute("value") == "7"
        assert browser.find_element(By.ID, "confirm-usual-basket").is_displayed()
        assert len(api(base, "/plans?limit=12")["plans"]) == 1
        assert api(base, "/household/history")["event_count"] == 1
        viewport_check(browser, "repeat usual basket")
        screenshot(browser, "15-usual-basket-repeat-with-updated-stock")
        print(
            "PASS usual basket: save, confirm, reload, one-tap repeat, "
            "fresh stock without silent writes",
            flush=True,
        )


def recipes_from_home(browser) -> None:
    """Unknown inventory, followed by observed shortfall and full-package quote."""
    with demo() as base:
        browser.get(base + "/")
        wait = WebDriverWait(browser, 20)
        wait.until(lambda d: d.find_element(By.CSS_SELECTOR, "#connection-status.online"))
        onboarding = browser.find_element(By.ID, "onboarding-layer")
        if onboarding.is_displayed():
            browser.find_element(By.ID, "onboarding-skip").click()
        panel = browser.find_element(By.ID, "recipes-panel")
        summary = panel.find_element(By.TAG_NAME, "summary")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
            summary,
        )
        summary.click()
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "#recipes-list .recipe-card"))
        first = browser.find_element(By.CSS_SELECTOR, "#recipes-list .recipe-card")
        assert "Нужно уточнить остатки" in first.text
        screenshot(browser, "16-recipes-unknown-stock")
        assert api(base, "/plans?limit=12")["plans"] == []

        api(base, "/household/stocktakes", {
            "event_id": "recipe-mobile-rice",
            "item_id": "rice",
            "quantity": {"amount": "1", "unit": "kg"},
            "reason": "fresh kitchen count",
        })
        api(base, "/household/stocktakes", {
            "event_id": "recipe-mobile-milk",
            "item_id": "milk",
            "quantity": {"amount": "100", "unit": "ml"},
            "reason": "fresh kitchen count",
        })
        refresh = browser.find_element(By.ID, "refresh-recipes")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
            refresh,
        )
        refresh.click()
        wait.until(lambda d: "Докупить: 1 поз." in d.find_element(
            By.ID, "recipes-list"
        ).text)
        first = browser.find_element(By.CSS_SELECTOR, "#recipes-list .recipe-card")
        summary = first.find_element(By.TAG_NAME, "summary")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
            summary,
        )
        summary.click()
        quote = first.find_element(By.CSS_SELECTOR, ".recipe-actions button")
        browser.execute_script(
            "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
            quote,
        )
        quote.click()
        wait.until(lambda d: "120 сом" in d.find_element(
            By.CSS_SELECTOR, "#recipes-list .recipe-quote"
        ).text)
        assert "предпросмотр" in browser.find_element(
            By.CSS_SELECTOR, "#recipes-list .recipe-quote"
        ).text
        assert api(base, "/plans?limit=12")["plans"] == []
        assert api(base, "/household/history")["event_count"] == 2
        viewport_check(browser, "cook-from-home quote")
        screenshot(browser, "17-recipes-price-quote-no-purchase")
        print("PASS recipes: unknown stock, fresh stock, package quote, no silent writes", flush=True)


def main() -> None:
    with mobile_browser() as browser:
        onboarding_wizard(browser)
        first_batch(browser)
        reminder_reentry(browser)
        routine_stand_preview(browser)
        recipes_from_home(browser)
    print("MOBILE_UX_SMOKE_OK", flush=True)


if __name__ == "__main__":
    main()
