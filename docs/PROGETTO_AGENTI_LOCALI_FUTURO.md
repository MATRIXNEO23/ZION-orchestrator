# Futuro: agenti locali deterministici (NON implementare ora)

**Priorità attuale:** completare Zion come orchestratore essenziale Claude ↔ Codex basato su hcom. Questo file è solo un appunto per una fase successiva.

## Obiettivo
Aggiungere piccoli agenti/script locali, ciascuno con una funzione specifica, eseguibili offline e senza API, modelli IA o token. Zion li avvia solo quando necessario; Claude/Codex intervengono esclusivamente per analisi o decisioni non gestibili con regole.

## Moduli candidati
- **Test:** esegue suite e restituisce esito, test falliti e percorso dei log.
- **Log:** filtra errori e duplicati; produce riferimenti essenziali.
- **Git/backup:** controlla stato e crea backup/versioni recuperabili.
- **File/diff:** rileva file modificati e genera differenze.
- **Stato:** registra compiti, esiti e avanzamento.

## Vincoli
- Riutilizzare messaggistica, eventi e SQLite di hcom; evitare duplicazioni.
- Script leggeri (es. Python), indipendenti, avviati on-demand; adatti a PC Windows con i3 e 10 GB RAM.
- Output strutturato minimo (stato, errori, percorsi); niente conversazioni tra agenti.
- Limiti di tempo, prevenzione dei cicli e operazioni distruttive solo previa conferma.
- Non aggiungere questi moduli prima di aver collaudato il coordinamento Claude/Codex.

**Nota per Claude:** questo è un promemoria progettuale sintetico, non una richiesta di implementazione immediata.
