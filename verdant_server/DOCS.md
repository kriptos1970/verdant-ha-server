# Verdant Server

Verdant Server sincronizza in modo privato piante, cronologia, misurazioni e
fotografie tra Verdant per macOS e iOS. Può inoltre leggere da Home Assistant
soltanto i sensori indicati esplicitamente nella configurazione.

## Configurazione

- `token`: token scelto per autenticare le app Verdant.
- `log_level`: livello dei messaggi del server.
- `max_photo_mb`: dimensione massima di una singola fotografia.
- `exposed_entities`: elenco degli identificativi Home Assistant autorizzati.
- `poll_interval_minutes`: frequenza di acquisizione automatica dei sensori.
- `ai_provider`: `none`, `gemini` oppure `openai` per il report giornaliero.
- `gemini_api_key` / `openai_api_key`: chiave del provider selezionato; rimane sul server.

Esempio:

```yaml
token: "un-token-lungo-e-casuale"
log_level: info
max_photo_mb: 20
  exposed_entities:
  - sensor.balcone_temperature
  - sensor.balcone_humidity
  - sensor.gerbera_soil_moisture
poll_interval_minutes: 15
ai_provider: none
```

Sono accettate esclusivamente entità numeriche con classe `temperature`,
`humidity`, `illuminance`, `moisture` o `conductivity`. Un'entità di altro tipo
non viene esposta anche se compare nella lista.

Il token interno di Home Assistant non viene mai restituito ai client Verdant.
Le chiavi AI non vengono mai restituite ai client e non sono necessarie per il
Care Engine deterministico.
Non pubblicare la porta 8099 su Internet; per l'accesso remoto usa una VPN.

## Eventi di cura API v2

Gli eventi v2 sono risorse indipendenti e non vengono più dedotti dalla sola
proprietà `history` della pianta. Gli endpoint richiedono la normale
autenticazione Bearer:

- `GET /v2/care-events?plantID=...`: snapshot degli eventi attivi;
- `GET /v2/care-events?includeDeleted=true`: snapshot comprensivo dei tombstone;
- `GET /v2/care-events/changes?since=...`: feed incrementale ordinato per sequenza;
- `PUT /v2/care-events/{id}`: creazione o aggiornamento idempotente;
- `DELETE /v2/care-events/{id}`: creazione di un tombstone, senza cancellazione fisica.

Ogni payload contiene almeno `id`, `plantID`, `kind`, `date`, `schemaVersion`,
`createdAt`, `updatedAt`, `revision` e `origin`. Il server confronta
`updatedAt`, `revision`, stato di eliminazione e hash canonico: una copia vecchia
non può quindi ripristinare un evento già eliminato. Gli endpoint v1 restano
disponibili durante la migrazione.
