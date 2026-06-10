from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import html
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


LOGIN_URL = (
    "https://logowanie.uek.krakow.pl/cas/login?"
    "service=https%3A%2F%2Fe-uczelnia.uek.krakow.pl%2Flogin%2Findex.php%3FauthCAS%3DCAS"
)


@dataclass(frozen=True)
class Config:
    login: str
    password: str
    api_key: str
    gemini_model: str
    conversation_id: str | None
    contact_name: str | None
    poll_seconds: float
    headless: bool
    auto_send: bool
    reply_to_own_messages: bool
    process_existing: bool
    process_last_visible: bool
    reset_state: bool
    max_messages_per_cycle: int | None
    state_path: Path
    max_recent_bot_answers: int
    bot_answer_similarity_threshold: float


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def xpath_literal(value: str) -> str:
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'

    parts = value.split('"')
    return "concat(" + ', \'"\' , '.join(f'"{part}"' for part in parts) + ")"


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {str(item) for item in data.get("seen_message_ids", [])}


def save_state(path: Path, seen_message_ids: Iterable[str]) -> None:
    data = {"seen_message_ids": sorted(set(seen_message_ids))}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_config(args: argparse.Namespace) -> Config:
    env = read_env(Path(args.env_file))
    required_keys = (
        "login",
        "password",
        "api_key",
        "DEFAULT_STATE_PATH",
        "DEFAULT_CONVERSATION_ID",
        "DEFAULT_CONTACT_NAME",
        "DEFAULT_GEMINI_MODEL",
        "MAX_RECENT_BOT_ANSWERS",
        "BOT_ANSWER_SIMILARITY_THRESHOLD",
    )
    missing = [key for key in required_keys if not env.get(key)]
    if missing:
        raise SystemExit(f"Brakuje w .env: {', '.join(missing)}")

    conversation_id = args.conversation_id or env["DEFAULT_CONVERSATION_ID"]
    contact_name = args.contact_name or env["DEFAULT_CONTACT_NAME"]

    return Config(
        login=env["login"],
        password=env["password"],
        api_key=env["api_key"],
        gemini_model=env["DEFAULT_GEMINI_MODEL"],
        conversation_id=conversation_id,
        contact_name=contact_name,
        poll_seconds=float(args.poll_seconds or env.get("poll_seconds", "12")),
        headless=args.headless or env.get("headless", "false").lower() == "true",
        auto_send=args.send or env.get("auto_send", "false").lower() == "true",
        reply_to_own_messages=args.reply_to_own_messages
        or env.get("reply_to_own_messages", "false").lower() == "true",
        process_existing=args.process_existing
        or env.get("process_existing", "false").lower() == "true",
        process_last_visible=args.process_last_visible
        or env.get("process_last_visible", "false").lower() == "true",
        reset_state=args.reset_state or env.get("reset_state", "false").lower() == "true",
        max_messages_per_cycle=args.max_messages_per_cycle,
        state_path=Path(args.state_path or env["DEFAULT_STATE_PATH"]),
        max_recent_bot_answers=int(env["MAX_RECENT_BOT_ANSWERS"]),
        bot_answer_similarity_threshold=float(env["BOT_ANSWER_SIMILARITY_THRESHOLD"]),
    )


def create_driver(headless: bool) -> WebDriver:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,1000")
    options.add_argument("--disable-notifications")
    options.add_argument("--no-first-run")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)


def wait(driver: WebDriver, seconds: int = 30) -> WebDriverWait:
    return WebDriverWait(driver, seconds)


def login_to_uek(driver: WebDriver, cfg: Config) -> None:
    driver.get(LOGIN_URL)
    username = wait(driver).until(EC.visibility_of_element_located((By.ID, "username")))
    password = wait(driver).until(EC.visibility_of_element_located((By.ID, "password")))

    username.clear()
    username.send_keys(cfg.login)
    password.clear()
    password.send_keys(cfg.password)
    wait(driver).until(EC.element_to_be_clickable((By.ID, "submitBtn"))).click()

    wait(driver, 60).until(
        lambda d: "e-uczelnia.uek.krakow.pl" in d.current_url
        or len(d.find_elements(By.CSS_SELECTOR, "i.fa-message")) > 0
    )


def click_messages(driver: WebDriver) -> None:
    icon = wait(driver, 45).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "i.fa-message"))
    )
    clickable = icon.find_element(By.XPATH, "./ancestor::*[self::a or self::button][1]")
    driver.execute_script("arguments[0].click();", clickable)


def open_conversation(driver: WebDriver, cfg: Config) -> None:
    selectors: list[tuple[By, str]] = []
    if cfg.conversation_id:
        selectors.append((By.CSS_SELECTOR, f"a[data-conversation-id='{cfg.conversation_id}']"))
    if cfg.contact_name:
        selectors.append(
            (
                By.XPATH,
                "//a[contains(@class, 'list-group-item')][.//strong[contains(normalize-space(.), "
                f"{xpath_literal(cfg.contact_name)}"
                ")]]",
            )
        )

    last_error: Exception | None = None
    for by, selector in selectors:
        try:
            target = wait(driver, 30).until(EC.element_to_be_clickable((by, selector)))
            driver.execute_script("arguments[0].click();", target)
            wait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-region='message']"))
            )
            return
        except (TimeoutException, NoSuchElementException) as exc:
            last_error = exc

    raise RuntimeError("Nie udalo sie otworzyc rozmowy.") from last_error


def message_id(element: WebElement) -> str:
    return element.get_attribute("data-message-id") or element.id


def message_text(element: WebElement) -> str:
    try:
        text_el = element.find_element(By.CSS_SELECTOR, "[data-region='text-container']")
        value = text_el.text.strip()
    except NoSuchElementException:
        value = element.text.strip()
    return re.sub(r"\s+", " ", value).strip()


def normalize_for_loop_detection(value: str) -> str:
    value = html.unescape(value).lower()
    value = re.sub(r"```[a-z0-9_-]*", "```", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def looks_like_recent_bot_answer(
    text: str,
    recent_bot_answers: Iterable[str],
    similarity_threshold: float,
) -> bool:
    normalized_text = normalize_for_loop_detection(text)
    if not normalized_text:
        return False

    for answer in recent_bot_answers:
        normalized_answer = normalize_for_loop_detection(answer)
        if not normalized_answer:
            continue
        if normalized_text == normalized_answer:
            return True
        if len(normalized_text) >= 80 and len(normalized_answer) >= 80:
            ratio = SequenceMatcher(None, normalized_text, normalized_answer).ratio()
            if ratio >= similarity_threshold:
                return True

    return False


def is_own_message(element: WebElement) -> bool:
    classes = f" {element.get_attribute('class') or ''} "
    return " send " in classes


def visible_messages(driver: WebDriver) -> list[WebElement]:
    return driver.find_elements(By.CSS_SELECTOR, "[data-region='message']")


def gemini_generate_answer(cfg: Config, incoming_message: str) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{cfg.gemini_model}:generateContent?key={cfg.api_key}"
    )
    prompt = f"""
You are a Polish-speaking specialist in "Wielowymiarowe modele ekonometrii finansowej".
Your core topics are: log returns, ARMA-GARCH, sGARCH, GJR-GARCH, Student/skewed
Student distributions, residual diagnostics, Ljung-Box tests, ARCH/GARCH effects,
DCC-GARCH, conditional covariance/correlation matrices, dynamic minimum-variance
portfolios, static portfolios, Value at Risk, VaR exceedances, and Kupiec tests.

Answer in Polish, briefly and concretely. Default to the shortest useful answer:
- simple fact/arithmetic: one sentence;
- simple R task: only the minimal R code, no long explanation;
- theory question: 2-5 concise bullet points or sentences;
- longer derivation/full script only if the user explicitly asks for it.

Do not add follow-up questions, motivation, background, or next steps unless asked.
When writing R code, make it ready to run and suited to the exact task. Prefer
standard packages and idioms for the problem, e.g. rugarch/rmgarch for GARCH and
DCC-GARCH. Keep comments sparse. Keep the reply ready to paste into Moodle chat.

NEW MESSAGE:
{incoming_message}
""".strip()

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.8,
            "maxOutputTokens": 10000,
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google API HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, http.client.InvalidURL, ValueError) as exc:
        raise RuntimeError(f"Nie mozna polaczyc sie z Google API: {exc}") from exc

    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Nieoczekiwana odpowiedz Google API: {data}") from exc

    answer = "\n".join(part.get("text", "") for part in parts).strip()
    return answer


def find_message_input(driver: WebDriver) -> WebElement:
    selectors = [
        "textarea[data-region='send-message-txt']",
        "textarea[data-region='text-input']",
        "textarea[placeholder*='Napisz']",
        "textarea",
        "[contenteditable='true']",
    ]
    for selector in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, selector)
        for element in elements:
            if element.is_displayed() and element.is_enabled():
                return element
    raise RuntimeError("Nie znaleziono pola wpisywania wiadomosci.")


def click_send_button(driver: WebDriver) -> bool:
    selectors = [
        "button[data-action='send-message']",
        "button[data-region='send-message']",
        "button[aria-label*='lij']",
        "button[title*='lij']",
    ]
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if element.is_displayed() and element.is_enabled():
                driver.execute_script("arguments[0].click();", element)
                return True
    return False


def send_chat_message(driver: WebDriver, answer: str) -> None:
    input_el = find_message_input(driver)
    input_el.click()

    if input_el.get_attribute("contenteditable") == "true":
        input_el.send_keys(answer)
    else:
        input_el.clear()
        input_el.send_keys(answer)

    if not click_send_button(driver):
        input_el.send_keys(Keys.CONTROL, Keys.ENTER)


def quit_driver(driver: WebDriver) -> None:
    try:
        driver.quit()
    except KeyboardInterrupt:
        print("Przerwano podczas zamykania ChromeDrivera.", flush=True)
    except Exception as exc:
        print(f"ChromeDriver byl juz zamkniety albo nie odpowiadal: {exc}", flush=True)


def process_new_messages(
    driver: WebDriver,
    cfg: Config,
    seen_message_ids: set[str],
    recent_bot_answers: list[str],
) -> None:
    messages = visible_messages(driver)
    if not messages:
        return

    handled = 0
    for element in messages:
        mid = message_id(element)
        if mid in seen_message_ids:
            continue

        seen_message_ids.add(mid)
        text = message_text(element)

        if is_own_message(element) and looks_like_recent_bot_answer(
            text,
            recent_bot_answers,
            cfg.bot_answer_similarity_threshold,
        ):
            print(
                f"Pomijam odpowiedz bota ({mid}), zeby nie zapetlic rozmowy. "
                f"Tresc: {text}",
                flush=True,
            )
            continue

        if is_own_message(element) and not cfg.reply_to_own_messages:
            print(
                f"Pomijam wlasna wiadomosc ({mid}). "
                "Do testow z tego samego konta uzyj --reply-to-own-messages. "
                f"Tresc: {text}",
                flush=True,
            )
            continue

        if not text:
            continue

        print(f"\nNowa wiadomosc ({mid}): {text}", flush=True)
        try:
            answer = gemini_generate_answer(cfg, text)
        except RuntimeError as exc:
            print(f"Blad generowania odpowiedzi dla {mid}: {exc}", flush=True)
            continue

        print(f"Odpowiedz: {answer}", flush=True)

        if cfg.auto_send:
            try:
                send_chat_message(driver, answer)
            except RuntimeError as exc:
                print(f"Blad wysylania odpowiedzi dla {mid}: {exc}", flush=True)
                continue
            recent_bot_answers.append(answer)
            del recent_bot_answers[:-cfg.max_recent_bot_answers]
            print("Wyslano odpowiedz.", flush=True)
        else:
            print("Tryb podgladu: nie wysylam. Uzyj --send, zeby wlaczyc wysylke.", flush=True)

        handled += 1
        if cfg.max_messages_per_cycle is not None and handled >= cfg.max_messages_per_cycle:
            return


def process_last_visible_message(
    driver: WebDriver,
    cfg: Config,
    seen_message_ids: set[str],
    recent_bot_answers: list[str],
) -> None:
    messages = visible_messages(driver)
    if not messages:
        return

    for element in messages[:-1]:
        seen_message_ids.add(message_id(element))

    last_message = messages[-1]
    last_message_id = message_id(last_message)
    seen_message_ids.discard(last_message_id)
    process_new_messages(driver, cfg, seen_message_ids, recent_bot_answers)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitoruje czat e-uczelnia UEK i przygotowuje odpowiedzi przez Google/Gemini API."
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--conversation-id", help="ID rozmowy z data-conversation-id.")
    parser.add_argument("--contact-name", help="Nazwa kontaktu widoczna na liscie rozmow.")
    parser.add_argument("--poll-seconds", type=float, help="Co ile sekund sprawdzac nowe wiadomosci.")
    parser.add_argument("--state-path", help="Plik z ID juz obsluzonych wiadomosci.")
    parser.add_argument("--headless", action="store_true", help="Uruchom Chrome bez okna.")
    parser.add_argument("--send", action="store_true", help="Wysylaj odpowiedzi automatycznie.")
    parser.add_argument(
        "--reply-to-own-messages",
        action="store_true",
        help="Odpowiadaj tez na wiadomosci oznaczone jako wyslane przez Ciebie.",
    )
    parser.add_argument(
        "--process-existing",
        action="store_true",
        help="Po otwarciu rozmowy przetworz tez wiadomosci, ktore juz sa widoczne.",
    )
    parser.add_argument(
        "--process-last-visible",
        action="store_true",
        help="Po otwarciu rozmowy przetworz tylko ostatnia widoczna wiadomosc.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Zignoruj zapisane ID obsluzonych wiadomosci i zacznij od pustego stanu.",
    )
    parser.add_argument(
        "--max-messages-per-cycle",
        type=int,
        help="Maksymalna liczba odpowiedzi w jednym sprawdzeniu, przydatne do testow.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = build_config(args)
    seen_message_ids = set() if cfg.reset_state else load_state(cfg.state_path)
    recent_bot_answers: list[str] = []

    driver = create_driver(cfg.headless)
    try:
        print("Logowanie do UEK...", flush=True)
        login_to_uek(driver, cfg)
        print("Otwieram wiadomosci...", flush=True)
        click_messages(driver)
        open_conversation(driver, cfg)

        if cfg.process_last_visible:
            process_last_visible_message(
                driver,
                cfg,
                seen_message_ids,
                recent_bot_answers,
            )
        elif cfg.process_existing:
            process_new_messages(
                driver,
                cfg,
                seen_message_ids,
                recent_bot_answers,
            )
        else:
            for element in visible_messages(driver):
                seen_message_ids.add(message_id(element))
            save_state(cfg.state_path, seen_message_ids)

        mode = "wysylka automatyczna" if cfg.auto_send else "podglad bez wysylania"
        print(f"Start monitorowania ({mode}). Ctrl+C konczy prace.", flush=True)
        while True:
            process_new_messages(
                driver,
                cfg,
                seen_message_ids,
                recent_bot_answers,
            )
            save_state(cfg.state_path, seen_message_ids)
            time.sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        print("\nZatrzymano.", flush=True)
        return 0
    finally:
        save_state(cfg.state_path, seen_message_ids)
        quit_driver(driver)


if __name__ == "__main__":
    sys.exit(main())
