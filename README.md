# Momentum Atelier

Applicazione locale di ricerca per una strategia mensile momentum / trend following multi-asset, long-only, in EUR. Interfaccia italiana, backtest total return, simulazione fiscale operativa, benchmark e archivio completo dei calcoli. Non invia ordini a broker.

## Uso online senza installazioni

La pubblicazione web è predisposta per Render sul piano gratuito, tramite `render.yaml` e `Dockerfile`. Il codice va usato dal ramo `codex/momentum-atelier`; occorre soltanto un account Render collegato a GitHub e l’approvazione della creazione del servizio. Non serve eseguire comandi sul proprio computer. Procedura nel browser: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Il server di sviluppo è verificato nel cloud, ma non è un sito pubblico: l’indirizzo HTTPS dell’app viene assegnato dal servizio di hosting dopo la pubblicazione.

## Avvio per sviluppatori

Requisiti: Linux (cloud) o macOS con Bash e Python 3.12+ e [uv](https://docs.astral.sh/uv/). Le dipendenze e le versioni sono in `pyproject.toml` e `uv.lock`.

```bash
cd /workspace/Prova
UV_CACHE_DIR=/workspace/.cache/uv uv sync --frozen
./scripts/start.sh
```

Aprire l'applicazione sulla porta 8000 nell'ambiente di esecuzione. Il server serve sia l'interfaccia sia l'API. `PORT=8080 ./scripts/start.sh` cambia porta. Per un'installazione fuori dal cloud omettere `UV_CACHE_DIR` o impostarlo su una cartella scrivibile locale. Nessuna chiave API necessaria per Yahoo.

```bash
.venv/bin/python -m pytest
```

La configurazione iniziale è in `config/default.json`. Le modifiche dei controlli dell'interfaccia si applicano alla singola analisi; l'export contiene la configurazione esatta. L'anagrafica immutata della specifica è in `config/instruments.json`.

## Dati e verifica

L'app parte **in modalità demo con dati sintetici deterministici**, chiaramente segnalata. Serve a esercitare tutti i percorsi e non rappresenta prezzi o risultati reali degli strumenti. Nessun fallback automatico da Yahoo a demo.

Per dati reali selezionare Yahoo e usare «Aggiorna dati». Il provider verifica simbolo, valuta EUR, mercato Milano e identità dichiarata, controlla le serie, conserva close, adjusted, dividendi, split e metadati/versione yfinance sotto `data/` (ignorata da Git). Un ticker errato/non disponibile interrompe l'aggiornamento; non viene sostituito. La valuta del fondo e l'esposizione valutaria sottostante sono diverse dalla valuta della quotazione. Non si applica alcuna conversione USD/EUR ulteriore, né si sottrae una seconda volta il TER.

Gli ISIN dell'anagrafica provengono dalla specifica dell'utente: non sono automaticamente certificati da Yahoo. La scheda dati distingue le verifiche del provider dai campi dichiarati. Non usare la demo come prova dell'anagrafica reale.

La policy cloud deve consentire `query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `fc.yahoo.com`, `guce.yahoo.com`, `consent.yahoo.com`, `finance.yahoo.com`; i registry delle dipendenze sono coperti dal preset package_managers. L'accesso è soggetto anche ai limiti del provider. Errori di rete o verifica restano visibili. La cache esistente non viene sostituita da un download incompleto.

## Regole implementate

- Score sulle chiusure mensili adjusted: rendimenti cumulati 3/6/12, media uniforme predefinita o variante .5/.3/.2; nessun mese saltato, nessuna annualizzazione prima della media.
- Storia consecutiva di 13 chiusure; score strettamente positivo; MA12 opzionale disattivata; parità risolte per ID crescente.
- Top N 2–6, default 6; pesi score (default), uniformi o inversa volatilità campionaria degli ultimi 12 rendimenti (`ddof=1`). Volatilità nulla/non calcolabile esclusa nella modalità inversa.
- Quota selezionata k/N, residuo C3M (o IBGX), anche con score negativo; se già selezionato i pesi si sommano. Universo completo reale o progressivo con minimo 10 disponibili predefinito.
- Segnale del mese completo t applicato al rendimento t+1. Nessun dato successivo usato per graduatoria/pesi. Il mese corrente è sempre provvisorio e distinto, mai aggiunto allo storico dei rendimenti interi.
- MSCI World SWDA B&H; 60/40 SWDA/AGGH ribilanciato annualmente a gennaio, con drift tra ribilanciamenti e periodo comune.

## Due calendari di simulazione

Il **Momentum lordo** è un modello mensile ideale che assume scambi alla chiusura del mese del segnale. Le sue metriche usano mesi interi: CAGR composto elevato a 12/M; volatilità campionaria annualizzata e drawdown su chiusure mensili, includendo il capitale iniziale nella baseline. Costi e banda sono applicati al modello operativo, non alla curva ideale.

La **contabilità operativa** usa invece il close negoziabile della prima seduta strettamente successiva al segnale. Ricostruisce quote frazionarie, cassa, costo medio e posizioni dopo il drift; paga costi/imposte dal patrimonio e risolve i target rispetto al patrimonio netto. Gli split aggiornano quantità e costo medio; i close Yahoo corretti per split vengono ricostruiti per la contabilità. Le distribuzioni sono accreditate e tassate alla data evento riportata da Yahoo, convenzionalmente usata come data incasso, e reinvestite al successivo ribilanciamento; non si duplica il dividendo nell'uso degli adjusted.

- **Momentum 1.0 standard:** ribilanciamenti parziali eseguiti economicamente, ma imposte sulle plusvalenze solo nelle uscite complete. È una simulazione fiscale semplificata.
- **Momentum Score:** imposte anche su vendite parziali. Aliquota iniziale 26%, plusvalenze riferite alle quantità vendute e al costo medio, nessuna compensazione delle minusvalenze. Distribuzioni tassate in entrambi.
- La banda facoltativa è una variante: congela le posizioni entro la soglia, ripartendo il patrimonio disponibile sui target restanti. Default zero.
- Costi in bps per controvalore (commissione + spread come costo separato); zero iniziale. Costi acquisto inclusi nella base fiscale, costi vendita dedotti dalla plusvalenza.

La tabella distingue valore prima/dopo una liquidazione equivalente. I benchmark operativi usano prezzi, costi, imposte e calendario coerenti; i benchmark lordi restano nel grafico teorico. Le curve mensili ideali e operative non sono economicamente identiche: non attribuire la loro differenza alle sole imposte.

Date personalizzate filtrano **solo mesi interi**. Per esempio, una data iniziale 22 luglio non produce una misura “dal 22 luglio”: il primo mese ammesso è agosto. La marcatura all'ultima quotazione e il segnale provvisorio sono separati dalle metriche di backtest. Nessuna simulazione intraday o esecuzione reale viene attribuita a un backtest mensile.

## Audit, conto reale e proxy

Scaricare l'archivio ZIP dopo un'analisi: contiene configurazione, anagrafica e provenienza, dati giornalieri raw/adjusted/dividendi/split, mesi completi, score e graduatorie, pesi, rendimenti, posizioni prima degli ordini, operazioni, costi, imposte, distribuzioni e liquidazione. Le ultime otto analisi restano in memoria fino al riavvio: scaricare gli archivi da conservare.

La scheda conto calcola TWR e XIRR da valorizzazioni datate **dopo** i flussi esterni (`external_flow` positivo per versamenti, negativo per prelievi). Per un TWR esatto serve una valorizzazione a ogni flusso, non solo un saldo iniziale/finale. Operazioni/dividendi interni non sono flussi esterni. XIRR considera investimento iniziale, versamenti/prelievi e saldo finale, con date reali. Mancanza di una radice unica produce un avviso, non un rendimento inventato. Sono metriche del conto, non una riconciliazione automatica di operazioni con la strategia; non confronta impropriamente rendimento/giacenza media con rendimento composto.

La scheda proxy concatena una storia fornita esplicitamente in un'**analisi separata**. Richiede fonte, esposizione, EUR, copertura, duration e trattamento distribuzioni, non inventa equivalenze; conserva i prezzi reali e raccorda rendimenti su un anchor contemporaneo al primo prezzo reale. Non modifica l'universo base né crea storia pregressa automaticamente.

Il repository iniziale conteneva soltanto `.gitkeep`: non esiste codice precedente da riprodurre o confrontare. k/N, benchmark e convenzioni fiscali derivano da questa specifica; non sono attribuiti a una vecchia implementazione. Ottimizzazioni, strategia “vendi solo all'uscita” e proxy certificati richiedono varianti separate, non sono simulati implicitamente.

API documentata dal server nel percorso `/docs`. Specifica originale in `docs/strategy-spec.txt`; contratti tecnici in `docs/CONTRACTS.md`. Le verifiche automatiche coprono segnali, look-ahead, fallback, pesi, ribilanciamenti, contabilità, metriche, TWR/XIRR, raccordi e export.

Verifiche e limiti osservati della consegna: [docs/VALIDATION.md](docs/VALIDATION.md). Per ripetere la prova dell’interfaccia, con server avviato e Chromium disponibile: `.venv/bin/python scripts/browser_smoke.py`. `./scripts/setup.sh` ripete installazione frozen e suite finanziaria/API.
