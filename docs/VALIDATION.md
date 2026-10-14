# Verifica della consegna — 14 ottobre 2026

Ambiente Linux x86_64, Python 3.12.14; dipendenze bloccate in uv.lock. Nessun codice precedente disponibile: checkout iniziale con il solo .gitkeep.

## Risultati

- `./scripts/setup.sh`: installazione frozen ripetuta correttamente; **106 test superati**, nessun fallimento o test saltato. Un avviso di deprecazione della dipendenza Starlette riguarda l'uso di httpx nei test, non un errore applicativo.
- Server avviato con `./scripts/start.sh`; health, catalogo, backtest e archivio ZIP verificati tramite richieste HTTP.
- `scripts/browser_smoke.py`: verificati grafico, configurazione Top 4/reattiva/inversa volatilità/MA12/costi, graduatoria, fiscalità, conto a saldo finale zero, raccordo proxy, download e layout 390px. Nessun errore JavaScript o overflow orizzontale. Screenshot ed export di prova in artifacts/ (ignorati da Git).
- Istruzioni startup salvate esercitate: health, backtest demo con pesi somma1, ledger operativo e ZIP.
- Demo sintetica default: **106 mesi completi**, dicembre 2017–settembre 2026. Allocazione dal segnale settembre; segnale ottobre e mark del 14 ottobre separati, nessun mese provvisorio nelle metriche.
- Yahoo progressivo minimo 10, default formule/pesi: **87 mesi completi**, luglio 2019–settembre 2026; **541 operazioni** per modello fiscale, 87 rendimenti mensili operativi per entrambi, cassa senza debito. Contabilità e benchmark verificati nel medesimo intervallo.
- Yahoo completo: errore esplicito di storia insufficiente (11 disponibili su 12). Non è un test superato dell'universo completo; è un limite del provider identificato. Nessuno strumento sostituito e nessun risultato demo presentato come reale.

## Prove finanziarie

Test su look-ahead, 13 chiusure consecutive, pesi nonnegativi/somma1, parità per ID, MA12, volatilità campionaria, quota k/N e fallback anche già selezionato, target dopo drift, benchmark 60/40 annuale e date personalizzate per mesi interi.

Fixture contabili verificano raw vs adjusted, scambi strettamente successivi al segnale, costo medio, quantità venduta, costi e plusvalenze, fiscalità uscite complete/parziali, distribuzioni, split, banda, riconciliazione indipendente della cassa e liquidazione finale. Calendario XMIL controlla cache parziali, Venerdì Santo e chiusura 31 dicembre.

Fixture metriche/conti/proxy verificano annualizzazione dei mesi interi, drawdown col capitale iniziale, TWR neutralizzazione flussi, XIRR e radici multiple, assenza di riempimenti e raccordo normalizzato. Review indipendente aggiuntiva dei moduli finanziari conclusa senza ulteriori anomalie materiali dopo le correzioni.

## Limiti dei dati

La cache contiene dati Yahoo e provenienza di 14 simboli. Simbolo, mercato MIL, valuta EUR, tipo e descrizione del prodotto sono verificati dal provider. **Gli ISIN sono dichiarati nella specifica e non comprovati da Yahoo.**

PHAU.MI: yfinance con periodo massimo e inizio 2000, e chart HTTPS nativo Yahoo con period1 esplicito, restituiscono una sola osservazione del 14 ottobre 2026. La qualità distingue listing verificato da storico total return non verificabile. Il backtest completo reale resta indisponibile finché lo strumento non offre storia sufficiente tramite la fonte scelta. Gaps nelle quotazioni sono conservati, non riempiti.

Fiscalità convenzionale e simulazione di esecuzione sono documentate in README; non certificano fiscalità effettiva, esecuzioni reali o risultati di un conto. L'estensione proxy è separata e richiede dati/metadati forniti esplicitamente. Nessuna strategia è stata ottimizzata per far coincidere rendimenti del conto e backtest.

## Configurazione cloud

Salvati nella bozza `install_script`, `start_skill` e domini Yahoo richiesti, preservando preset e impostazioni esistenti. Il salvataggio non dimostra pubblicazione o ripristino in un nuovo task. La macchina corrente è stata verificata; l'utente deve rivedere/salvare le impostazioni e pubblicare per conservarne la versione preparata. Il server va riavviato nei nuovi task: i processi non sono parte dello snapshot.

## Preparazione della pubblicazione web

Aggiunti Dockerfile con dipendenze frozen, utente non root e porta dinamica; Blueprint Render sul piano gratuito, una istanza e un worker, nel ramo `codex/momentum-atelier`. Il container è stato provato con un limite di 512 MB: health check, interfaccia, catalogo, backtest e ZIP. Il certificato del proxy di build è un mount temporaneo e non compare nel runtime. Il download Yahoo ora avviene come job in background con stato verificabile, per evitare richieste HTTP lunghe: casi riuscito/fallito e serializzazione verificati nei test e nell’interfaccia.

La pubblicazione su Render richiede la creazione dell’account e il collegamento GitHub dell’utente. La preparazione Docker e del repository non dimostra che un sito pubblico esista già. Procedura senza comandi locali in [DEPLOYMENT.md](DEPLOYMENT.md).
