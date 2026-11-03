# MLX Control

<img src="docs/screenshot-menu.png" width="360" alt="Pannello di MLX Control">

**Menu Bar Extra per macOS che rende MiniMax Code usabile con un modello locale.**

MLX Control installa e governa uno stack completo e **tutto locale**:

- un **server MLX** (Apple Silicon) che espone LFM3 e altri modelli MLX con API OpenAI-compatible;
- la registrazione del provider `mlx` dentro **MiniMax Code**, così il modello locale diventa un
  provider custom selezionabile come qualsiasi altro;
- una **compattazione del contesto** alternativa, basata su [Rizzo Flow](https://github.com/Rizzo-AI-Academy/rizzo-flow),
  che rende la "Compacting context" da minuti a pochi secondi;
- una **Menu Bar Extra** per accendere/spegnere, cambiare modello e parametri, e installare i modelli
  con un clic.

Nessun dato esce dal computer: modello, compattazione e cache restano sulla macchina.

---

## Indice

- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Come funziona](#come-funziona)
- [Compattazione del contesto](#compattazione-del-contesto)
- [La Menu Bar Extra](#la-menu-bar-extra)
- [Comandi da terminale](#comandi-da-terminale)
- [Dove stanno i file](#dove-stanno-i-file)
- [Configurazione di MiniMax Code](#configurazione-di-minimax-code)
- [Problemi frequenti](#problemi-frequenti)
- [Sviluppo](#sviluppo)
- [Licenza](#licenza)

---

## Requisiti

| | |
|---|---|
| macOS | 13.0 o successivo, Apple Silicon (M1+) |
| MiniMax Code | installato e avviato almeno una volta |
| Spazio | ~1,2 GB per LFM3-1.7B-4bit, ~1,8 GB per Rizzo Flow |
| Tool di sistema | Xcode Command Line Tools (`xcode-select --install`) |

L'installer si occupa di installare da solo **Homebrew**, **Python 3.11+** e **uv** se mancano.
Se Homebrew non è installato e l'installazione automatica fallisce (serve la password di
amministratore), l'installer stampa il comando da eseguire a mano.

## Installazione

```bash
git clone https://github.com/dex-community/mlx-control.git
cd mlx-control
./install.sh
```

L'installer, in ordine:

1. controlla Homebrew, Xcode Command Line Tools, Python 3.11+ e `uv`, installando quello che manca;
2. crea gli ambienti Python (server MLX con `mlx-lm`, Rizzo Flow con `uv`);
3. scarica il modello **LFM3-1.7B-MLX-4bit** in `~/.mlxControl/models`;
4. scrive i LaunchAgent (server MLX, Rizzo Flow, proxy di compattazione, pannello);
5. registra il provider `mlx` in `~/.minimax/config.yaml` e imposta il modello predefinito;
6. compila e installa la Menu Bar Extra in `/Applications/MLX Control.app` con la sua icona.

Opzioni:

```bash
./install.sh --no-rizzo     # salta Rizzo Flow (resta la compattazione nativa)
./install.sh --no-model     # non scarica il modello MLX
./install.sh --skip-deps    # non tocca Homebrew/Python/uv
```

## Come funziona

MiniMax Code parla con i provider tramite la configurazione in `~/.minimax/config.yaml`.
MLX Control aggiunge un provider custom OpenAI-compatible:

```yaml
custom_provider:
  mlx:
    name: LFM3 MLX (locale)
    kind: custom
    enabled: true
    api: openai-completions
    options:
      baseURL: http://127.0.0.1:8080/v1
      authMode: api-key
      apiKey: sk-mlx-local
    models:
      lfm3-1.7b-mlx:
        name: LFM3-1.7B-MLX-4bit (locale)
        tool_call: true
        limit:
          context: 262144
          output: 8192
```

Con il provider registrato, `LFM3-1.7B-MLX-4bit` compare nella lista dei modelli di MiniMax Code
e può essere scelto come **modello predefinito** dal pannello ("Agente predefinito → LFM3 locale").

> Il supporto LFM3 in `mlx-lm` arriva dalla PR [#2016](https://github.com/ml-explore/mlx-lm/pull/2016),
> non ancoramerged: l'installer la installa direttamente da GitHub.

Il server viene poi **patchato** da `scripts/patch-mlx-server.py` (idempotente, con copia del file
originale in `server.py.mlxcontrol-orig`): alias dei modelli, parametri di campionamento da
variabili d'ambiente, endpoint `/v1/responses/input_tokens`, rimozione dei tag `<think>`, hint di
sistema e **riparazione degli argomenti dei tool** secondo lo schema dichiarato.

La riparazione serve ai modelli piccoli: un 1.7B spesso produce una chiamata `ask_user` quasi
corretta ma con forme sbagliate (`options: ["a", "b"]` invece di una lista di oggetti, oppure
l'intero array serializzato come stringa JSON). Il runtime rifiuta la chiamata e il modello
ripete la stessa domanda all'infinito. Il server corregge la forma prima di rispondere e la
chiamata viene finalmente accettata.

Il server MLX è avviato da un LaunchAgent e riparte da solo se muore. Il pannello lo riavvia
automaticamente se all'avvio non risponde.

## Compattazione del contesto

Quando il contesto si riempie, MiniMax Code chiede al **modello di sessione** di riscrivere la
conversazione in un checkpoint. Con un modello locale da 1,7B l'operazione è lentissima: la
richiesta contiene l'intera conversazione (oltre 100.000 caratteri) e il modello deve generare
fino a 8.192 token.

MLX Control offre tre modalità, selezionabili dal pannello:

| Modalità | Come funziona | Velocità (conversazione da 57 messaggi, 113.000 caratteri) |
|---|---|---|
| **Nativa MiniMax** | il checkpoint lo scrive il modello di sessione | **80,5 s** (1.398 token generati) |
| **Rizzo Flow** | Rizzo Flow decide in locale cosa scartare, il modello scrive un checkpoint breve | **15,4 s** |
| **Rizzo senza LLM** | Rizzo Flow decide cosa tenere, il checkpoint è composto senza generare nulla | **8,9 s** (0 token) |

Come è fatto:

1. il proxy (`fast_compact.py`) intercetta la richiesta di checkpoint riconoscendo il marcatore
   `Checkpoint control: summarize the preceding conversation now` e la passa al modello solo dopo
   averla ridotta;
2. **Rizzo Flow** (`http://127.0.0.1:8017`, API Jev-compatibile `POST /v1/systemone`) risponde a
   domande chiuse "questa unità di messaggi serve ancora?" con una **probabilità**, senza generare
   un solo token;
3. i messaggi superflui vengono eliminati (a coppie: una `tool_call` e il suo `tool_result` se
   ne vanno insieme), gli output dei tool superstiti vengono troncati;
4. a questo punto il modello riceve un input molto più piccolo e genera un checkpoint breve;
   nella modalità "senza LLM" il checkpoint a otto sezioni viene composto direttamente.

L'algoritmo di selezione è un porting in Python di
[`fast-jev-compaction`](https://github.com/tamaratran/fast-jev-compaction) (MIT), con l'endpoint
Jev-compatibile di Rizzo Flow al posto dell'API cloud TypeSafe: **nessuna chiave esterna**.

Dettagli:

```bash
# configurazione attuale del proxy
cat ~/.minimax/local-models/fast-compact.json

# statistiche dell'ultima compattazione
cat ~/.mlxControl/state/last-compaction.json
```

| chiave | significato |
|---|---|
| `mode` | `native`, `rizzo` o `rizzo-only` |
| `level` | `fast` (decisioni per unità di messaggi) o `deep` (decisioni per singola tool call) |
| `max_tokens` | tetto di token del checkpoint |
| `tool_chars` | caratteri tenuti per output di tool |
| `model` | alias del modello da usare per il checkpoint (vuoto = modello di sessione) |
| `rizzo_autostart` | avvia Rizzo Flow quando serve (default `true`) |
| `rizzo_idle_seconds` | secondi di inattività dopo cui Rizzo Flow viene spento (default `900`) |
| `mlx_memory_guard` | riavvia il server MLX quando è inattivo e occupa troppa memoria (default `true`) |
| `mlx_memory_threshold_bytes` | soglia di memoria del server MLX (default `3.500.000.000`, ≈ 3,5 GB) |
| `mlx_memory_guard_idle_seconds` | inattività minima prima del riavvio (default `90`) |

## Memoria: perché il server occupa così tanto

Il server MLX gira sulla **memoria unificata** del Mac: pesi del modello, cache KV del contesto e
cache dei prompt condividono la RAM con tutto il resto del sistema. Su un Mac da 16 GB, una
conversazione lunga può portare il processo a **10-12 GB** — è il comportamento atteso di MLX, ma
niente di necessario.

MLX Control lo tiene sotto controllo in tre modi:

1. **Cache dei prompt limitata** (`model-settings.json`): il default di `mlx_lm` è 3 GB di cache
   conservate tra le richieste; qui è **512 MB** con 2 voci. La cache serve al riutilizzo del
   prefisso: a valle della compattazione il contesto cambia, quindi non serve a nulla.

   ```json
   { "prompt_cache_size": 2, "prompt_cache_bytes": 536870912 }
   ```

2. **Rizzo Flow on demand**: il motore di decisioni tiene in memoria un modello da 1,7 GB
   (≈ 2 GB residenti). Non serve sempre, quindi il proxy di compattazione lo **avvia alla prima
   richiesta** e lo **spegne dopo 15 minuti di inattività** (`rizzo_idle_seconds`). Nel pannello
   Rizzo Flow compare come "pronto · si avvia da solo".

3. **Memory guard**: dopo una compattazione MLX continua a tenere i buffer del contesto elaborato
   (per restituire la memoria al sistema serve riavviarlo). Il proxy riavvia il server MLX **solo
   quando è davvero inattivo** e la memoria supera la soglia (3,5 GB per default).

   Il stato di attività viene letto **dal server stesso** (`GET /admin/stats` → `generating`), non
   dal proxy: il traffico può arrivare anche da altri client (per esempio il proxy Caveman, che
   inoltra direttamente a `:8080`), e riavviare il server mentre una generazione è in corso
   interromperebbe la risposta del modello.

   ```bash
   curl -s http://127.0.0.1:8080/admin/stats       # solo lettura: memoria + generazioni in corso
   curl -s http://127.0.0.1:8080/admin/clear-cache  # mx.clear_cache()
   curl -s http://127.0.0.1:8081/memory             # memoria del server MLX in byte
   ```

Misure reali su questo Mac (16 GB, LFM3-1.7B-MLX-4bit):

| situazione | prima | dopo |
|---|---|---|
| server MLX a riposo | 1,1 GB | 1,1 GB |
| dopo una richiesta da 49k token | 4,7 GB | **2,9 GB** |
| dopo una richiesta da 231k token | 6,2 GB | **≈ 3,4 GB** |
| Rizzo Flow in inattività | 2,0 GB sempre attivo | **0 (si spegne)** |

> Nota: la quantizzazione della KV cache (`kv_bits`) **non** è compatibile con LFM3: il modello
> usa una `RotatingKVCache` ibrida e la quantizzazione non è implementata
> (`RotatingKVCache Quantization NYI`). Per questo il tetto si applica alla cache dei prompt.

## La Menu Bar Extra

![Pannello](docs/screenshot-menu.png)

Dalla barra dei menu:

- **Servizi** — accendi/spegni modello, proxy Caveman, Rizzo Flow e compattazione veloce;
  nella barra del titolo compare la memoria occupata dal server MLX;
- **Modello** — modello attivo e catalogo Hugging Face con ricerca e download integrato;
- **Agente predefinito** — scegli se l'agente usa il modello locale o MiniMax M3.1 Flash;
- **Compressione del contesto** — Nativa / Rizzo Flow / Rizzo senza LLM, con installazione di Rizzo
  Flow e statistiche dell'ultima compattazione;
- **Parametri modello** — temperature, top-k, min-p, repetition penalty, max output token, thinking;
- **Finestra di contesto** — da 32K a 256K (input + output), con riallineamento delle sessioni aperte;
- **Consigliati / Riavvia / Apri log / Apri cartella / Esci**.

Ogni parametro si applica subito: il pannello salva il file di configurazione e riavvia il server.

## Comandi da terminale

```bash
~/.minimax/local-models/lfm3-mlx.sh status      # stato del server MLX
~/.minimax/local-models/lfm3-mlx.sh restart     # riavvio
~/.minimax/local-models/lfm3-mlx.sh test        # richiesta di prova
~/.minimax/local-models/lfm3-mlx.sh set-context 262144
~/.minimax/local-models/lfm3-mlx.sh fix-sessions

~/.minimax/local-models/scripts/rizzo-flow.sh install|start|stop|status|logs
~/.minimax/local-models/scripts/fast-compact.sh start|stop|restart|status|logs

~/.minimax/local-models/scripts/models.py list-installed
~/.minimax/local-models/scripts/models.py list-remote
~/.minimax/local-models/scripts/models.py download mlx-community/Qwen2.5-0.5B-Instruct-4bit
~/.minimax/local-models/scripts/models.py set-active <percorso>

~/.minimax/local-models/scripts/set-compression.py native|rizzo|rizzo-only
~/.minimax/local-models/scripts/register-provider.py --default-model local
~/.minimax/local-models/scripts/patch-mlx-server.py --check   # stato delle patch
```

## Dove stanno i file

Tutti i percorsi sono dinamici e vivono nella home dell'utente:

```
~/.mlxControl/                 cartella nascosta dell'applicazione
├── models/                    modelli MLX scaricati (es. LiquidAI/LFM3-1.7B-MLX-4bit)
├── rizzo-flow/                clone di Rizzo Flow + pesi Spark-X2.5
├── logs/                      log dei servizi
└── state/                     statistiche dell'ultima compattazione

~/.minimax/local-models/        script e configurazione del runtime
├── mlxenv/                    ambiente Python con mlx-lm
├── model-settings.json        parametri del modello
├── fast-compact.json          configurazione del proxy di compattazione
├── current-model.txt          modello attivo
└── scripts/                   modelli.py, rizzo-flow.sh, set-compression.py, …

~/Library/LaunchAgents/         com.minimax.lfm3-mlx-server, .rizzo-flow, .fast-compact, .lfm3control
/Applications/MLX Control.app  la Menu Bar Extra
```

Le variabili d'ambiente `MLX_CONTROL_DIR` e `LFM3_INSTALL_DIR` permettono di spostare tutto altrove.

## Configurazione di MiniMax Code

MiniMax Code conserva la configurazione in `~/.minimax/config.yaml`. MLX Control la modifica in
modo chirurgico (backup automatico a ogni scrittura) e **non tocca mai le chiavi API** esistenti.

```bash
# provider locale + modello predefinito
~/.minimax/local-models/scripts/register-provider.py --default-model local

# compattazione veloce
~/.minimax/local-models/scripts/set-compression.py rizzo-only
```

Dopo aver cambiato provider o modello predefinito **riavvia MiniMax Code**.

### La lista dei modelli è dinamica

Non c'è da modificare `config.yaml` a mano: la lista dei modelli registrati in MiniMax Code segue
automaticamente i modelli installati in `~/.mlxControl/models`.

- **`mlx`** (server MLX diretto) e **`mlx-caveman`** (modello + Caveman) vengono aggiornati
  entrambi, con le stesse voci e gli stessi limiti.
- Ogni modello riceve una chiave stabile derivata dal nome della cartella
  (es. `LiquidAI/LFM3-1.7B-MLX-8bit` → `lfm3-1.7b-mlx-8bit`); la chiave storica
  `lfm3-1.7b-mlx` resta sempre valida e punta al modello attivo, così il modello predefinito e
  il proxy di compattazione non si interrompono quando si cambia modello.
- Il server MLX espone ogni modello come alias (`MLX_LM_MODEL_ALIASES`): scegliendo un modello
  diverso dal selettore, il server lo carica su richiesta.

La sincronizzazione avviene:

- **con un clic**: pulsante *Sincronizza modelli* nella sezione *Modello* del pannello;
- **in automatico**: all'avvio del pannello, dopo un download o un cambio di modello attivo, e a
  ogni controllo periodico (max ogni 5 minuti);
- **da terminale**:

```bash
~/.minimax/local-models/scripts/sync-models.py            # sincronizza
~/.minimax/local-models/scripts/sync-models.py --check    # dice solo se serve (exit 1)
~/.minimax/local-models/scripts/sync-models.py --aliases  # alias per il server MLX
```

Il file `~/.mlxControl/model-aliases.env` è generato dallo stesso script e letto da
`start-mlx-server.sh` al riavvio del server.

## Problemi frequenti

**Il modello non compare nella lista.** Riavvia MiniMax Code: la configurazione viene letta all'avvio.
Verifica con `curl http://127.0.0.1:8080/v1/models`.

**La compattazione resta lenta.** Controlla che il proxy sia attivo
(`~/.minimax/local-models/scripts/fast-compact.sh status`) e che la modalità sia `rizzo` o
`rizzo-only` (`cat ~/.minimax/local-models/fast-compact.json`).

**MiniMax Code non parte con il modello locale.** Il modello da 1,7B è pensato per task brevi: per
sessioni lunghe conviene il modello cloud. Nel pannello: "Agente predefinito → MiniMax M3.1 Flash".

**Errore `model ... is unavailable in the current catalog`.** Il `defaultModel` punta a un modello
non registrato: rilancia `register-provider.py --default-model local`.

**"Context compaction failed".** Il checkpoint non è arrivato al runtime. Controlla
`~/.mlxControl/logs/fast-compact.error.log`: se la richiesta è stata servita (`200`) ma il runtime
l'ha rifiutata, il problema è nella risposta. La versione corrente scrive lo stream SSE con il
framing chunked corretto (prima il terminatore `[DONE]` finiva fuori dai chunk e il parser HTTP
del runtime segnalava un errore, con 40 s di attesa).

**Il modello ripete la stessa domanda all'infinito.** Succede quando una sua chiamata a un tool
viene rifiutata dal runtime (per esempio `ask_user` con gli argomenti malformati): il modello
riprova identico. La patch del server (`scripts/patch-mlx-server.py`) corregge gli argomenti
prima che lascino il server: per applicarla rilancia `~/.minimax/local-models/install.sh` e poi
riavvia il server MLX.

**`POST_ADMISSION_FAILED` durante la sessione.** La finestra di contesto della sessione è più vecchia
di quella configurata: esegui `lfm3-mlx.sh fix-sessions` e riavvia MiniMax Code.

## Sviluppo

```bash
./build.sh                      # compila e installa la Menu Bar Extra
swift tools/make-icon.swift build/LFM3Control.iconset   # rigenera l'icona
python3 tools/make-screenshot.py                        # rigenera gli screenshot
python3 tools/test-tool-repair.py                       # test della riparazione degli argomenti dei tool
```

Struttura:

```
src/LFM3Control.swift    pannello SwiftUI
src/Models.swift         catalogo Hugging Face e download
scripts/                 script di installazione e gestione
config/                  default dei file di configurazione
tools/                   icona e screenshot
docs/                    immagini per il README
```

## Licenza

MIT — vedi [LICENSE](LICENSE).

I modelli scaricati (LFM3, Spark-X2.5) restano soggetti alle rispettive licenze.
