# Pubblicazione online

La configurazione è pronta. Il servizio **non è ancora pubblicato**: occorrono la registrazione gratuita su Render e il collegamento del proprio account GitHub. Non sono necessarie installazioni sul computer dell'utente.

## Render: accesso tramite browser

Il file [`render.yaml`](../render.yaml) prepara un servizio web Docker sul piano gratuito, con controllo di salute `/api/health`, una sola istanza e deploy automatici disattivati.

1. Registrarsi su [Render](https://render.com/) e collegare il proprio account GitHub quando richiesto.
2. Scegliere **New → Blueprint**, selezionare `oresteant-hash/Prova` e il ramo `codex/momentum-atelier`, quindi verificare il piano gratuito proposto prima di creare il servizio.
3. Attendere la build. Render assegna `PORT`; il container ascolta su `0.0.0.0` e usa quella porta.
4. Aprire l'indirizzo HTTPS assegnato da Render. `/api/health` deve rispondere con `status: ok`; la pagina iniziale permette di eseguire la demo sintetica e l'aggiornamento Yahoo.

Il piano gratuito può sospendere il servizio inattivo e richiedere tempo al primo accesso. La sua disponibilità dipende dalle condizioni dell'account Render; la configurazione non attiva un piano a pagamento.

## Dati e riavvii

L'immagine non contiene cache Yahoo, esportazioni, repository Git o credenziali locali. La demo genera dati sintetici ed è utilizzabile appena il servizio parte. Le quotazioni reali vengono scaricate esclusivamente con **Aggiorna Yahoo** e conservate nella cartella scrivibile `/opt/momentum/data`.

Sul piano gratuito la cache è temporanea e può scomparire al riavvio o a un nuovo deploy. Le analisi e i relativi ZIP sono conservati in memoria dal processo: scaricare un'esportazione prima di riavviare il servizio. Per conservare la cache tra i deploy occorre configurare separatamente uno storage persistente con il proprio hosting; questo Blueprint non lo acquista.

Yahoo deve essere raggiungibile via HTTPS su `query1.finance.yahoo.com` e `query2.finance.yahoo.com`. Non sono previste chiavi API. L'accesso al provider può essere limitato dall'hosting e uno storico insufficiente viene segnalato, senza sostituire strumenti o usare la demo come dato reale.

## Altri hosting Docker

Il [`Dockerfile`](../Dockerfile) usa Python 3.12, l'immagine ufficiale `uv:0.12.23` e digest SHA-256 fissati per entrambe le immagini. Installa le dipendenze da `uv.lock` con `--frozen`, mantiene le verifiche TLS e usa un utente applicativo non root (`10001`). Il server usa un solo worker perché analisi ed esportazioni sono conservate in memoria.

Lo stesso container può essere pubblicato su un hosting compatibile con Docker, una variabile `PORT` e un endpoint HTTPS. Il certificato di un eventuale proxy di build può essere fornito come mount BuildKit temporaneo `build_ca_bundle`: non è necessario su Render e non viene copiato nell'immagine finale.

Verifica eseguita nel cloud: build completata, processo con utente `10001`, limite di memoria di 512 MB, porta assegnata tramite `PORT`, health check positivo, pagina e catalogo disponibili, backtest demo di tre mesi e ZIP esportato. Il container di verifica è stato arrestato; questa verifica non pubblica un sito.
