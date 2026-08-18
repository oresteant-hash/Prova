# Atlas Market Regime 3.1 Live

Dashboard locale che aggiorna prezzi (Yahoo Finance) e indicatori macro (FRED) senza chiavi API.

## Accesso rapido su Windows

1. Scarica o copia **tutta la cartella** dell'app sul PC.
2. Verifica che [Python](https://www.python.org/downloads/) sia installato. Durante l'installazione seleziona **Add Python to PATH**.
3. Fai doppio clic su **`avvia-atlas.bat`**.
4. Attendi due secondi: Atlas si aprirà automaticamente nel browser.

Se il browser non si apre, visita manualmente <http://127.0.0.1:8765>. La finestra del server deve rimanere aperta mentre usi Atlas.

## Avvio manuale (Windows, macOS o Linux)

Apri il terminale nella cartella dell'app ed esegui:

```bash
python app.py
```

Su alcuni computer Windows il comando è invece:

```powershell
py app.py
```

Apri quindi <http://127.0.0.1:8765> nel browser. Per arrestare il server torna nel terminale e premi **Ctrl+C**.

> Non aprire direttamente `index.html` con `file://`: il server è necessario per scaricare i dati evitando i limiti CORS del browser.

## Aggiornamento dati

Il pulsante **Aggiorna dati** forza un nuovo download; normalmente i dati sono memorizzati in cache per un'ora. La porta e la cache si configurano con le variabili d'ambiente `ATLAS_PORT` e `ATLAS_CACHE_SECONDS`.

> Strumento informativo, non consulenza finanziaria. Le fonti possono essere temporaneamente indisponibili e i dati possono essere ritardati.
