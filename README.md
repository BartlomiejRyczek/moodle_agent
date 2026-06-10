# UEK chat bot

Prosty bot do czatu e-uczelnia UEK. Loguje sie przez Selenium, otwiera wskazana rozmowe i generuje odpowiedzi przez Google/Gemini API.

Agent jest ustawiony jako pomocnik do przedmiotu Wielowymiarowe modele ekonometrii finansowej: GARCH, DCC-GARCH, portfele, VaR, test Kupca i kod R. Nie wczytuje dodatkowych notatek ani historii.

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
