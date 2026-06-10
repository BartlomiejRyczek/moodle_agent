# UEK chat bot

Prosty bot do czatu e-uczelnia UEK. Loguje sie przez Selenium, otwiera wskazana rozmowe i generuje odpowiedzi przez Google/Gemini API.

Agent jest ustawiony jako pomocnik do przedmiotu Wielowymiarowe modele ekonometrii finansowej: GARCH, DCC-GARCH, portfele, VaR, test Kupca i kod R. Nie wczytuje dodatkowych notatek ani historii.

Jeśli chcesz by pomagał Ci w innej dziedzinie zmień system prompt fragment kodu odpowiadający za to prompt:

```
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
"""
```

## Konfiguracja

Logujemy sie do https://aistudio.google.com w prawym górnym rogu kilkamy create API key, tworzymy nazwe i kopiujemy klucze.

Nastepnie w repozytorium tworzymy plik .env według schematu ponizej i wpisujemy tam klucze, login do moodla, haslo do moodla.

W `.env`:

```env
login=...
password=...
api_key=...
DEFAULT_STATE_PATH=.uek_chat_seen.json
DEFAULT_CONVERSATION_ID=
DEFAULT_CONTACT_NAME=
DEFAULT_GEMINI_MODEL=gemini-3.5-flash
MAX_RECENT_BOT_ANSWERS=8
BOT_ANSWER_SIMILARITY_THRESHOLD=0.92
```

## Instalacja

```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Jeśli python nie działa na windows spróbuj tego:
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```


## Uruchomienie
Testowanie na swoich wiadomosciach:

```powershell
.\.venv\Scripts\python.exe .\uek_chat_bot.py --send --reply-to-own-messages
```

Bot zapamietuje ostatnie wyslane przez siebie odpowiedzi i pomija podobne wiadomosci, zeby nie odpisywac sam sobie.
