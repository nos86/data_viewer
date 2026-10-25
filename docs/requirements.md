# Viewer misure — Specifica dei requisiti

Oct 25, 2026 · @Salvo Musumeci

## Contesto e vincoli

Webapp per visualizzare e analizzare acquisizioni di misura da file binari proprietari, ispirata funzionalmente ad AVL Concerto (non ne replica la UI). Esecuzione primaria in locale, estendibile a Databricks App.

| Vincolo | Valore |
| --- | --- |
| Stack | Backend FastAPI (Python), frontend Vue 3 + TypeScript |
| Input | File binario + funzione Python esistente che restituisce un DataFrame pandas (timestamp, canale, valore) e un JSON di metadati; in sviluppo è sostituita da un mock |
| Volume | Oltre 50M campioni per file |
| Durata misura | Al massimo 3 h |
| Risoluzione massima | 10 Hz per canale |
| Deploy | Locale; in seguito Databricks App |

**Glossario**

- **Reticolo master:** passo Δ = 0,1 s, origine t0 = 0 all'inizio di ogni misura.
- **k:** indice intero sul reticolo master (t = k·Δ).
- **n:** passo di un canale in multipli di Δ (n = 1 → 10 Hz, n = 10 → 1 Hz).
- **Modo step:** ultimo valore noto. **Modo linear:** interpolazione tra i due campioni adiacenti. **Modo average:** media dei campioni nativi nella finestra centrata sul punto di griglia, \[t\_k − n·Δ/2, t\_k + n·Δ/2).
- **Canale a evento:** canale senza periodo regolare (inviato al cambiamento).
- **File primario:** misura di riferimento per il tempo dei cursori.

## Requisiti funzionali

Ciò che l'utente può fare.

**Misure e tempo**

- **RF-01** L'utente carica un file binario dal browser oppure sceglie misure dalla tabella Delta tramite i metadati; può aprirne più di una e la prima è il file primario. Il selettore filtra per vin, configurationId e data di inizio.
- **RF-02** Ogni misura ha un asse del tempo relativo con t0 = 0 all'inizio della misura; tutte le misure condividono lo stesso asse.
- **RF-03** Una misura si sposta di n secondi; il valore si arrotonda a 0,1 s e la UI mostra lo spostamento applicato. Sono ammessi tempi negativi.
- **RF-04** Comando "imposta t0 sul cursore".
- **RF-05** Dove una misura è terminata, i suoi canali risultano "fuori misura", non con l'ultimo valore.

**Canali**

- **RF-06** Il catalogo mostra per ogni canale: frequenza nativa, frequenza assegnata, flag evento, modo suggerito.
- **RF-07** L'utente sceglie i canali e, per ciascuno, il modo step, linear o average (quest'ultimo non per i canali a evento). Default: step per i canali a evento, linear per gli altri.
- **RF-08** Ogni canale mantiene la propria frequenza, con limite a 10 Hz. Nessun canale viene portato a una frequenza superiore alla nativa.

**Layout e grafici**

- **RF-09** Il layout ha n tab; ogni tab può contenere fino a 4 grafici impilati in verticale.
- **RF-10** L'area di disegno è allineata in verticale tra tutti i grafici della stessa tab.
- **RF-11** Ogni grafico ha un numero di divisioni verticali configurabile.
- **RF-12** Assi Y multipli; più canali possono condividere un asse. Per ogni asse: lato (sx/dx), banda di divisioni occupata, min/max manuali o automatici.
- **RF-13** Tick ed etichette compaiono solo nella banda dell'asse. Assi sovrapposti sullo stesso lato vengono sfalsati in orizzontale.
- **RF-14** Etichette con 3 cifre significative, decimali uniformi per asse e tick sempre distinguibili tra loro.
- **RF-15** Per ogni linea: colore, spessore, tipo di tratto, marker.
- **RF-16** I marker si mostrano su tutti i punti. Oltre una soglia di punti visibili compare un avviso da accettare; l'accettazione vale per la sessione; il rifiuto disattiva i marker di quella serie.
- **RF-17** Legenda in alto a ogni grafico; con più misure aperte, ogni serie porta un'etichetta breve della misura (A, B…).
- **RF-18** Grafici XY: i punti sono ai tempi comuni dei due canali, solo valori misurati. L'XY occupa uno dei 4 posti di una tab e non partecipa all'allineamento dell'asse del tempo; un cursore singolo appare come punto evidenziato con il suo numero, una banda evidenzia il tratto corrispondente.

**Zoom**

- **RF-19** Zoom e pan sono sincronizzati tra i grafici di una tab.
- **RF-20** Lo zoom è locale alla tab, con l'opzione di estenderlo a tutte le tab con l'azione singola "applica questo zoom a tutte le tab".

**Cursori**

- **RF-21** Cursori singoli e a banda, in numero illimitato.
- **RF-22** I cursori sono globali: visibili e allineati su tutti i grafici di tutte le tab.
- **RF-23** Il tempo del cursore è quello del file primario; il cursore si sposta di un passo (0,1 s) e si aggancia al reticolo.
- **RF-24** Ogni cursore ha un numero visibile sull'asse del tempo; il colore è opzionale.
- **RF-25** Un cursore fuori vista è segnalato sul bordo; il comando "vai al cursore" ricentra la vista.
- **RF-26** La legenda mostra i valori del cursore attivo. Una tabella mostra i canali della tab corrente per tutti i cursori.
- **RF-27** Per una banda: min, max, media, valore iniziale e finale, Δ, durata, con segnalazione dei buchi di acquisizione.

**Formule**

- **RF-28** Editor a blocchi con quattro tipi: canale fisico, costante, operatore, canale calcolato.
- **RF-29** Ogni blocco mostra il proprio risultato intermedio.
- **RF-30** I collegamenti che creano un ciclo vengono impediti.
- **RF-31** Un canale mancante si sostituisce con un altro canale, una costante o un canale calcolato. L'alias viene proposto solo in quel momento ed è facoltativo.
- **RF-32** Layout e formule si salvano come file JSON (template) gestito dall'utente; il backend non lo conserva. Misure aperte, shift e cursori non si salvano su file ma sopravvivono al ricaricamento della pagina.
- **RF-33** Il backend fornisce gli operatori; le formule di uso comune vengono promosse a nuovi operatori.

**Interfaccia, export e upload**

- **RF-34** Ricerca dei canali per nome con filtro; assegnazione di un canale a grafico e asse tramite trascinamento o menu.
- **RF-35** Export dei dati visibili (canali della tab, finestra di zoom corrente) in CSV e Parquet, in formato long (tempo, canale, valore): ogni canale mantiene la sua griglia, nessun valore riempito.
- **RF-36** Export di un grafico come immagine PNG e della tab intera in PDF, con legende e tabella dei cursori.
- **RF-37** Upload con avanzamento visibile di caricamento e conversione; messaggi d'errore chiari per file corrotti o non riconosciuti dall'estrattore; dimensione massima configurabile.
- **RF-38** Interfaccia in inglese. Separatore decimale secondo la lingua del browser; date e ore nel fuso orario del browser.
- **RF-39** Comandi da tastiera: frecce = cursore attivo di un passo, Maiusc + frecce = dieci passi; scorciatoie per zoom e cambio tab.
- **RF-40** Colori assegnati in automatico ai nuovi canali da una palette distinguibile anche da chi ha difficoltà con i colori.

## Requisiti implementativi

Il backend calcola, il frontend presenta e orchestra; nessuna trasformazione dei dati vive nel frontend.

&#91;embedded content: architettura · 7 componenti\]

Il worker converte il binario una sola volta; da lì in poi il servizio legge solo il Parquet e il core non dipende né da FastAPI né dalla cache.

**Architettura backend**

- **RI-01** Tre livelli: *core* (operatori come funzioni pure numpy + registro), *servizio* (lettura Parquet, griglie, allineamento, cache), *API* (endpoint FastAPI sottili).
- **RI-02** Backend senza stato di sessione; la cache è solo un'ottimizzazione.
- **RI-03** Storage astratto: filesystem locale; Unity Catalog Volumes nella fase Databricks. Due sorgenti di misure: binario caricato dal browser e tabella Delta (KVT) con tabella di metadati. Configurazione solo via variabili d'ambiente o file.

**Ingestione**

- **RI-04** L'estrattore restituisce un DataFrame pandas con l'intera misura (timestamp assoluto, canale, valore) e un JSON di metadati. Gira in un processo separato, così la memoria si libera a fine conversione. Il wrapper converte il canale in categorico, scrive il DataFrame a fette da 1M righe (configurabile) come row group di un Parquet temporaneo e lo rilascia; DuckDB ordina poi con un limite di memoria impostato. Il picco di memoria è quello del DataFrame intero: su Databricks, se supera la taglia dell'app, l'estrazione dei binari gira come job su cluster.
- **RI-05** Conversione binario → Parquet una sola volta, con cache basata sull'hash del file. Upload dal browser a blocchi (es. 50 MB) con ripresa; binario e Parquet restano nello storage. Le misure Delta si materializzano nello stesso Parquet alla prima apertura: la KVT (key, value, timestamp) si legge a blocchi come l'output dell'estrattore; la tabella metadati (path, vin, configurationId, startOfSampling, endOfSampling) è collegata tramite uuid. L'uuid è l'id della misura e la chiave della cache; per il binario la chiave è l'hash del file. t0 = startOfSampling e fine = endOfSampling; per il binario t0 = primo timestamp del DataFrame e fine = ultimo timestamp. I metadati del binario vengono dal JSON dell'estrattore (misura, path, vin, datetime, lista dei segnali); configurationId non è disponibile. La lista dei segnali alimenta il catalogo canali prima che la conversione finisca.
- **RI-06** Parquet in formato long (channel, t, value): channel con codifica a dizionario, t intero in µs, value float64. Ordinato per (channel, t) con DuckDB, che esegue l'ordinamento con spill su disco.
- **RI-07** Catalogo canali: nome, numero campioni, t\_min/t\_max, periodo nativo (mediana dei Δt), flag evento, min/max del valore, n assegnato, modo suggerito.
- **RI-08** Valori booleani convertiti in 0/1; valori non numerici esclusi e segnalati. Timestamp duplicati: vale l'ultimo.

**Base tempi e griglie**

- **RI-09** Reticolo master Δ = 100.000 µs, t0 = inizio misura; tutta l'aritmetica dei tempi è intera. I timestamp assoluti di DataFrame e KVT sono in ora locale: all'ingestione si convertono in UTC con il fuso della sorgente (default Europe/Rome, configurabile). Le ore ambigue del ritorno all'ora solare si risolvono per canale dall'ordine dei campioni (tz\_localize di pandas con ambiguous='infer'); se l'inferenza fallisce, la conversione si interrompe con un errore esplicito.
- **RI-10** n ammessi in catena di divisibilità: 1, 2, 10, 20, 100, 200… (10; 5; 1; 0,5; 0,1; 0,05 Hz). Si assegna il più piccolo n della catena ≥ 0,9 × periodo nativo / Δ (nessun upscaling, 10% di tolleranza al jitter).
- **RI-11** Griglia del canale: k = j·n, ancorata a t0. Valori calcolati dai dati nativi: step = ultimo campione con t ≤ t\_k (`join_asof` backward); linear = `numpy.interp` con left/right = NaN; average = media dei campioni nativi nella finestra centrata \[t\_k − n·Δ/2, t\_k + n·Δ/2), finestra vuota = null. Average non è disponibile per i canali a evento.
- **RI-12** `max_gap`: un campione mancante produce subito un buco. Se tra due campioni nativi consecutivi passano più di 1,5 periodi nativi (margine per il jitter, configurabile), i punti di griglia in quell'intervallo sono null in tutti i modi. Nessun limite per i canali a evento.
- **RI-13** Canali a evento: n = 1, modo step di default.
- **RI-14** Calcolo su richiesta dei soli canali selezionati; cache LRU con limite di memoria, chiave (file, canale, n, modo, fase).

**API**

| Endpoint | Ingresso | Uscita |
| --- | --- | --- |
| GET /files | — | Misure da tabella metadati Delta e file caricati |
| POST /uploads | Binario a blocchi, con ripresa | Id della misura |
| POST /files/{id}/ingest | — | Stato della conversione |
| GET /files/{id}/channels | — | Catalogo canali |
| POST /files/{id}/series | Canali, modo per canale | Serie |
| GET /operators | — | Catalogo operatori |
| POST /operators/{nome} | Ingressi (canali, handle, costanti), parametri, shift per file | Handle + serie |

- **RI-15** Contratto delle serie: comune `step_base`; per serie `file, canale, n, j_start, values[]`. Array contigui, null nei buchi. Formato colonnare (Arrow IPC o JSON per colonne, scelto con benchmark). Valori float64.

**Operatori**

- **RI-16** Registro con decoratore. Metadati: nome, versione, descrizione, ingressi, parametri (tipo, default, range), tipo d'uscita (continuo/discreto), natura (puntuale/temporale), flag di deprecazione.
- **RI-17** Uscita sulla griglia n = max(n degli ingressi). Gli ingressi più veloci si sottocampionano per indice: solo valori misurati. Tra misure con shift non allineati si usano solo i punti coincidenti; se sono pochi o nessuno, la UI lo segnala.
- **RI-18** Le costanti si estendono sulla griglia degli altri ingressi. Errore se tutti gli ingressi sono costanti, o se un operatore temporale riceve una costante.
- **RI-19** I null si propagano; gli operatori temporali ricevono il passo della griglia.
- **RI-20** Handle = hash di (operatore, versione, ingressi, parametri, modi, shift). Handle sconosciuto → errore esplicito; il frontend ricalcola la catena.
- **RI-21** Una formula promossa a operatore ha un test di equivalenza permanente nella suite.

**Frontend**

- **RI-22** Libreria grafica con serie a x indipendenti (es. uPlot in mode 2), scelta con benchmark.
- **RI-23** Editor a blocchi (es. Vue Flow). Orchestrazione: ordinamento topologico, rilevamento cicli, chiamate parallele sui rami indipendenti.
- **RI-24** Calcoli ammessi solo di presentazione: lettura al cursore, statistiche di banda, shift sugli indici, layout.
- **RI-25** Allineamenti sempre sull'indice intero k, mai su tempi in virgola mobile.
- **RI-26** Margini sx/dx = massimo tra i grafici della tab. Corsie assegnate per lato; larghezza da min/max formattati (segno incluso), ricalcolata solo a modifica della configurazione. Con `auto` si usa il range dell'intero canale. Bande adiacenti sullo stesso lato condividono la corsia, omettendo l'etichetta superiore della banda inferiore.
- **RI-27** Formattatore unico per tick, larghezze, legenda e tabella: decimali = max(3 cifre significative del valore massimo, decimali del passo dei tick). Notazione esponenziale se |v| ≥ 10⁶ oppure 0 < |v| < 10⁻³. Nessuna unità di misura: la KVT non le fornisce.
- **RI-28** Asse su banda \[a, b\] di D divisioni: h = (max − min)/(b − a); range = \[min − a·h, max + (D − b)·h\].
- **RI-29** Cursori su un livello sovrapposto, separato dal rendering dei dati.
- **RI-30** Le tab non visibili non caricano dati.
- **RI-31** Legenda con voci a larghezza fissa; altezza calcolata alla configurazione, non durante il movimento del cursore.
- **RI-32** File JSON: `schema_version`, versione degli operatori, riferimenti tipizzati (`ch` / `calc`), file autosufficienti, posizioni grafiche separate dal grafo. Lo stato di sessione (misure aperte, shift, cursori, zoom, tab attiva, accettazione marker) sta in sessionStorage: sopravvive al refresh e si cancella alla chiusura della scheda. Dopo il refresh le misure si riaprono per id dalla cache Parquet, senza nuova conversione.

**Astrazione della base dati**

La base dati può essere Databricks o il PC locale dell'utente: il backend vi accede solo tramite quattro interfacce, e cambiare base dati significa cambiare adattatore, senza toccare core o servizio. Il frontend non ha astrazioni: lo gestiamo interamente fino alla visualizzazione.

- **RI-33** Interfacce definite nel codice del backend come `typing.Protocol`. Core e servizio dipendono solo da queste; le librerie di accesso ai dati (deltalake, SDK Databricks, filesystem) si importano solo negli adattatori.
- **RI-34** L'adattatore si sceglie da configurazione, si costruisce all'avvio e si passa con l'iniezione delle dipendenze di FastAPI. Passare da locale a Databricks non richiede modifiche al codice.
- **RI-35** Nessun oggetto specifico della base dati (client SDK, sessioni, cursori SQL) attraversa un'interfaccia: passano solo strutture dell'app.
- **RI-36** Ogni interfaccia ha una suite di test di contratto, eseguita su tutti gli adattatori, e un'implementazione in memoria per i test.
- **RI-37** Il contratto tra backend e frontend è lo schema OpenAPI generato da FastAPI; i tipi TypeScript del client si generano da questo schema. L'API è versionata (`/api/v1`).

| Interfaccia | Responsabilità | Adattatore PC locale | Adattatore Databricks |
| --- | --- | --- | --- |
| MeasurementCatalog | Cerca misure (vin, configurationId, date) e ne restituisce i metadati per uuid | Tabella metadati in una cartella locale; elenco degli upload | Tabella metadati Delta in Unity Catalog; elenco degli upload |
| MeasurementSource | Per uuid restituisce blocchi (key, value, timestamp), t0 e fine misura | KVT in una cartella locale; estrattore sui binari caricati | Tabella KVT Delta; estrattore sui binari caricati |
| BlobStorage | Scrive, legge ed elenca file (upload, binari, Parquet) | Filesystem locale | UC Volume |
| SeriesStore | Scrive i blocchi ordinati, legge canali su un intervallo | Parquet + DuckDB su disco locale | Parquet + DuckDB su Volume |

**Operatività e qualità**

- **RI-38** Le misure della KVT sono immutabili: la cache usa l'uuid come chiave, senza controllo di versione.
- **RI-39** Binari caricati e cache Parquet hanno un limite di spazio configurabile; oltre la soglia si eliminano i meno usati (LRU). Le misure aperte in una sessione attiva non si eliminano.
- **RI-40** Su Databricks KVT e metadati si leggono con l'identità dell'utente, così valgono i permessi Unity Catalog. Prima di servire una misura dalla cache condivisa si verifica che l'utente possa leggerla. La modalità di accesso per conto dell'utente supportata dalle Databricks Apps va verificata.
- **RI-41** Installazione locale come pacchetto Python: `pip install` e un comando di avvio che apre il browser. Il frontend compilato è incluso nel pacchetto.
- **RI-42** Test di correttezza su un dataset di riferimento con risultati attesi per griglie, modi step/linear, shift, operatori ed export, oltre ai test di contratto.
- **RI-43** Pipeline CI/CD su Azure DevOps: test, build del pacchetto, deploy della Databricks App; versionamento semantico dell'app.
- **RI-44** Log strutturati nel backend con id di richiesta; gli errori di ingestione riportano file, blocco e causa.

**Criteri di accettazione delle prestazioni**

Target di accettazione, da verificare con le misure della fase 1.

| Scenario | Target |
| --- | --- |
| Ingestione di un binario da 50M campioni (PC locale) | ≤ 1 min |
| Materializzazione di una misura KVT da 50M campioni | ≤ 1 min |
| Apertura di un canale veloce, primo accesso | ≤ 2 s |
| Apertura di un canale già in cache | ≤ 0,2 s |
| Zoom e pan con 4 grafici × 10 canali | Risposta ≤ 500 ms |
| Canali per grafico | Fino a 20 |
| Misure aperte insieme | Fino a 4 |

## Limiti noti e punti da misurare

Cinque limiti accettati per scelta di progetto e sei valori da misurare nella fase 1.

**Limiti accettati**

- **Fenomeni sopra 10 Hz:** i canali veloci sono ridotti al valore istantaneo sulla griglia; picchi e transitori brevi possono sparire, anche zoomando. Il modo average riduce l'aliasing, ma media i picchi invece di conservarli. Evoluzione possibile: un operatore di inviluppo min/max che legge i dati grezzi.
- **Risoluzione temporale 0,1 s:** misurare ritardi tra segnali con precisione maggiore non è possibile dalla UI; i timestamp nativi restano solo nel Parquet.
- **Orchestrazione delle formule solo nel frontend:** un futuro calcolo batch richiederà un esecutore Python, poche righe con `graphlib` se il formato JSON resta stabile.
- **Accesso ai dati su Databricks:** i permessi per utente sui Volumi vanno verificati quando si affronta quella fase. Lo stesso per la lettura della tabella Delta in locale (SQL warehouse oppure deltalake direttamente dallo storage).
- **Cambio d'ora:** una misura a cavallo del ritorno all'ora solare dipende dall'inferenza sull'ordine dei campioni; un canale senza campioni nell'ora ripetuta non è risolvibile in modo certo.

**Da misurare nella fase 1**

- Tempo e picco di memoria dell'ingestione di un file da 50M campioni.
- Latenza di `POST /series` per un canale veloce, senza e con cache.
- Formato di trasferimento: Arrow IPC contro JSON per colonne.
- Rendering di 4 grafici con molti canali da 108.000 punti ciascuno.
- Soglia di punti visibili per l'avviso sui marker.
- Tempo di lettura di una misura dalla KVT: dipende dal partizionamento o clustering per uuid.

## Fonti

- [Databricks Apps, dimensioni di calcolo](https://learn.microsoft.com/en-us/azure/Databricks/dev-tools/databricks-apps/compute-size)
- [DuckDB, workload più grandi della memoria](https://duckdb.org/docs/current/guides/performance/how_to_tune_workloads)
- [DuckDB, statistiche dei row group Parquet](https://duckdb.org/docs/current/data/parquet/tips)
- [pyarrow ParquetWriter](https://arrow.apache.org/docs/14.0/python/generated/pyarrow.parquet.ParquetWriter.html)
- [Polars join\_asof](https://docs.pola.rs/api/python/stable/reference/lazyframe/api/polars.LazyFrame.join_asof.html)
- [numpy.interp](https://numpy.org/doc/stable/reference/generated/numpy.interp.html)
- [uPlot, modalità dei dati](https://leeoniya.github.io/uPlot/docs/data-modes.html)
- [Python graphlib](https://docs.python.org/3/library/graphlib.html)
- [Vue Flow](https://www.mintlify.com/bcakmakoglu/vue-flow)
- [deltalake (delta-rs) per Python](https://delta-io.github.io/delta-rs/python/)
- [sessionStorage, documentazione MDN (copia W3cubDocs)](https://docs.w3cub.com/dom/window/sessionstorage.html)
- [FastAPI, alternative e principi (OpenAPI, iniezione delle dipendenze)](https://FastAPI.tiangolo.com/alternatives/)
- [Specifica di typing.Protocol (PEP 544)](https://typing.readthedocs.io/en/latest/spec/protocol.html)
- [pandas Series.tz\_localize](https://pandas.pydata.org/docs/reference/api/pandas.Series.tz_localize.html)
