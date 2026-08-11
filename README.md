# Verdant Server

Server privato per sincronizzare Verdant tra macOS e iOS tramite la rete locale o la VPN WireGuard del FRITZ!Box.

Il progetto è separato dall'app Apple e viene distribuito come app (add-on) Home Assistant. Usa SQLite per i dati, il filesystem per le fotografie e conserva tutto nel volume persistente `/data` gestito da Home Assistant.

La sincronizzazione fotografica comprende l'immagine principale delle piante, tutte le immagini del diario di crescita, tutte le sessioni fotografiche di controllo salute e le immagini dei fertilizzanti. Il database e la cartella `/data/photos` vengono quindi conservati insieme quando il backup Home Assistant include Verdant Server.

## Struttura

```text
VerdantServer/
├── verdant_server/
│   ├── config.yaml
│   ├── Dockerfile
│   ├── run.sh
│   ├── app/
│   └── tests/
└── README.md
```

## API iniziale

- `GET /health`: stato del server, senza autenticazione.
- `GET /v1/sync?since=0`: modifiche successive a una sequenza.
- `GET /v1/entities/{collection}`: elenco degli elementi non eliminati.
- `PUT /v1/entities/{collection}/{id}`: creazione o aggiornamento.
- `DELETE /v1/entities/{collection}/{id}`: eliminazione logica.
- `PUT /v1/photos/{id}`: caricamento di una fotografia.
- `GET /v1/photos/{id}`: download di una fotografia.

Tutte le rotte `/v1` richiedono `Authorization: Bearer <token>`.

Le collezioni iniziali sono `plants`, `fertilizers`, `care-events` e `growth-entries`. Il campo `payload` mantiene il modello Codable dell'app e permette di evolvere lo schema senza migrazioni distruttive immediate.

## Installazione locale su Home Assistant OS

1. Installare e configurare l'app ufficiale **Samba share** oppure **Terminal & SSH**.
2. Copiare la cartella `verdant_server` dentro `/addons/verdant_server` sul server Home Assistant.
3. Aprire **Impostazioni → App → App store** e scegliere **Controlla aggiornamenti** dal menu.
4. Installare **Verdant Server**.
5. Inserire un token lungo e casuale nella configurazione.
6. Avviare l'app e abilitare **Avvio automatico** e **Watchdog**.

Il server ascolta sulla porta TCP `8099`. Non inoltrare questa porta sul router: fuori casa deve essere raggiunta soltanto attraverso WireGuard.

## Sviluppo locale

```bash
cd verdant_server
python3 -m unittest discover -s tests -v
```

Per eseguire l'API fuori da Home Assistant occorrono le dipendenze elencate in `app/requirements.txt` e le variabili `VERDANT_TOKEN`, `VERDANT_DATA_DIR` e `VERDANT_PORT`.
