# Changelog

## 0.4.9

- Sincronizza il catalogo completo dei ricordi fotografici tramite UUID stabili.
- Propaga cestino e ripristino tra Mac e iOS.
- Aggiunge la cancellazione definitiva dei file fotografici scaduti.
- Mantiene la compatibilità con foto profilo e diario delle versioni precedenti.

## 0.4.8

- Registra nella cronologia l'orario di acquisizione del ciclo Verdant, così un polling impostato a 60 minuti produce voci coerenti ogni ora.

## 0.4.7

- Valuta la luminosità in base all'ora locale: di sera e di notte non produce falsi suggerimenti di miglioramento luce.
- Rimanda le valutazioni correttive della luce alla fascia diurna 08:00–18:00 (Europe/Rome).

## 0.4.6

- Espone la cronologia sensori completa per pianta, inclusi i sensori ambientali associati alla stanza.
- Deduplica e ordina le misurazioni usate dalla sezione “Andamento e cronologia”.

## 0.4.5

- Include nelle raccomandazioni il valore sensore realmente usato, la sorgente e l'ora della lettura.
- Permette alle app di distinguere chiaramente l'ora della lettura dall'ora della valutazione.

## 0.4.4

- Esegue l'aggiornamento manuale dei piani AI in background e restituisce subito HTTP 202.
- Aggiorna automaticamente lo stato nel pannello, evitando i timeout Cloudflare 524.
- Mostra correttamente lo stato `running` durante l'elaborazione.

## 0.4.3

- Impedisce aggiornamenti AI manuali e pianificati concorrenti.
- Rilegge la versione più recente della pianta prima di salvare il piano AI.
- Ritenta in sicurezza il salvataggio in caso di modifica contemporanea dall'app.

## 0.4.2

- Aggiunge il pannello web “Pianificazione AI” all'interfaccia dell'add-on.
- Aggiunge il pulsante “Aggiorna ora” e la visualizzazione di provider, modello, esito, orari ed errori.

## 0.4.1

- Aggiunge frequenza e ora configurabili per l'aggiornamento automatico dei piani AI.
- Registra ultimo tentativo, ultimo successo, esito, errore e prossima esecuzione.
- Migra il provider Gemini al modello stabile `gemini-2.5-flash-lite`.
- Corregge l'inizializzazione del logger e la ripresa dopo un riavvio.

## 0.4.0

- Sposta sul server il campionamento periodico dei sensori e il Care Engine deterministico.
- Espone raccomandazioni per singola pianta e per l'intera collezione.
- Aggiunge il report giornaliero opzionale con Gemini o OpenAI.
- Rende configurabili intervallo di polling e provider AI dall'add-on Home Assistant.
- Le raccomandazioni globali vengono sempre calcolate sulle misurazioni più recenti.

## 0.3.4

- Evita di trasferire nuovamente fotografie invariate usando checksum SHA-256 ed ETag.
- Aggiunge richieste HEAD e download condizionali per la sincronizzazione fotografica.

## 0.3.3

- Legge i sensori autorizzati direttamente dalle opzioni persistenti del Supervisor.
- Allinea la versione restituita dall'endpoint di stato.

## 0.3.2

- Aggiunge il supporto effettivo alla collezione sincronizzata `measurements`.

## 0.3.1

- Corregge la serializzazione dell'elenco `exposed_entities` all'avvio dell'add-on.

## 0.3.0

- Espone a Verdant soltanto i sensori Home Assistant autorizzati.
- Limita i dati alle classi ambientali e del substrato supportate.
- Sincronizza le associazioni tra sensori, posizioni e piante.
- Aggiunge le capacità `measurements`, `home-assistant-sensors` e `sensor-mappings`.

## 0.2.0

- Aggiunge la sincronizzazione dei profili biologici delle specie.
- Estende la sincronizzazione di fotografie e misurazioni.
