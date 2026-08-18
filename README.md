# Atlas Market Regime 3.1 Live

Dashboard locale che aggiorna prezzi (Yahoo Finance) e indicatori macro (FRED) senza chiavi API.

## Avvio

```bash
python app.py
```

Aprire <http://127.0.0.1:8765>. Non aprire direttamente `index.html` con `file://`: il server è necessario per scaricare i dati evitando i limiti CORS del browser.

Il pulsante **Aggiorna dati** forza un nuovo download; normalmente i dati sono memorizzati in cache per un'ora. La porta e la cache si configurano con `ATLAS_PORT` e `ATLAS_CACHE_SECONDS`.

> Strumento informativo, non consulenza finanziaria. Le fonti possono essere temporaneamente indisponibili e i dati possono essere ritardati.
