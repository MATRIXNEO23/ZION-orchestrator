# ZION-orchestrator

**ZION 0.1.0 — prima versione da collaudare su Windows.**

Orchestratore minimale per **Claude Code (sviluppo)** e **Codex CLI (revisione)**. Usa [hcom](https://github.com/aannoo/hcom) come motore esterno per messaggistica e cronologia SQLite: **non duplica né modifica il motore Rust di hcom**.

## Funzionamento

1. Invia a Claude una richiesta di sviluppo.
2. Attende un breve rapporto JSON locale con identificativo univoco.
3. Invia a Codex soltanto l'obiettivo, i file interessati e un riepilogo.
4. Se Codex trova errori, inoltra a Claude soltanto i problemi concreti.
5. Si ferma a esito positivo, in presenza di blocchi o dopo **massimo 3 revisioni**. Interrompe anche gli errori identici ripetuti.

Nessuna terza IA: ZION usa soltanto Python standard library. hcom gestisce messaggi e SQLite sul computer. Il trasporto dei messaggi non richiede API a pagamento, ma **Claude e Codex consumano la propria quota quando leggono ed elaborano richieste**. Il risparmio di token dipende dai compiti e non è garantito.

## Requisiti

- Windows 10/11, connessione Internet per installazioni e IA. Su Windows e necessario **WinGet / App Installer** per l'installazione automatica dei prerequisiti mancanti.
- Python 3.10+, Git, Claude Code CLI, Codex CLI e hcom sono necessari per il funzionamento. **`INSTALLA_ZION.bat` prova a installare soltanto quelli assenti**.
- Claude e Codex richiedono autenticazione personale e un accesso valido ai rispettivi servizi; verificare separatamente abbonamenti e fatturazione.
- ZION non chiede credenziali o chiavi API e non installa nuovi componenti in modalita `/check`.

## Installazione e diagnosi automatica su Windows (consigliata)

Scarica il repository come ZIP da GitHub, estrailo in una cartella e fai doppio clic su **`INSTALLA_ZION.bat`**.

- Controlla **Git, Python 3.10+, hcom, Claude Code CLI e Codex CLI**; se mancano, prova a installarli attraverso WinGet, uv o npm dai canali di distribuzione ufficiali. Se gia presenti e funzionanti, li lascia invariati.
- Scrive i risultati e gli errori in **`logs/setup_DATA_ORA.log`**, senza avviare le due IA o consumare quota di modelli.
- Se un'installazione non riesce (per esempio WinGet non disponibile, privilegi insufficienti o incompatibilita), segnala l'errore e non avvia automaticamente il lavoro tra agenti.
- L'accesso agli account Claude e Codex deve essere completato **manualmente**: ZION non raccoglie credenziali e non garantisce l'accesso ai servizi.

Per una **sola diagnostica senza installazioni**, fai partire `INSTALLA_ZION.bat /check` dal Prompt dei comandi. Sul PC possono apparire richieste di conferma di Windows durante le installazioni.

L'installer e pensato **solo per Windows** e va ancora verificato sul tuo PC reale; non assicura che ogni combinazione di hardware, policy aziendali o account sia compatibile.

## Interfaccia grafica ZION (Windows)

Esegui **AVVIA_ZION.bat** per aprire la finestra grafica, senza usare i comandi nel CMD.

- Tre pannelli: **Codex a sinistra, ZION al centro, Claude a destra**. Vengono mostrati gli eventi hcom e il coordinamento effettivo. Non viene inventato output di agenti; i transcript completi non sono ancora visualizzati.
- Le frecce tra i pannelli si animano quando viene inviato un incarico o ricevuto un rapporto.
- Scegli la cartella Git del progetto, inserisci il compito e premi **Avvia lavoro**. Le richieste partono **solo dopo conferma** e possono consumare quota dei modelli.
- Il pulsante **Errori e diagnostica** apre in qualunque momento una finestra dedicata, aggiornata automaticamente; in caso di errore si apre da sola. Il file completo è salvato nella cartella **logs** e resta disponibile anche dopo l'uscita.
- **Ferma ZION** interrompe il processo coordinatore, ma non forza la chiusura delle sessioni Claude/Codex eventualmente già avviate. Non vengono cancellati database, file del progetto o cronologie.
- Il collegamento hcom viene avviato sequenzialmente in modalità headless: è consigliato avere hcom **0.7.31 o successivo** per gli eventi live e l'output JSON. L'app non installa automaticamente aggiornamenti o modifica le credenziali.
- **AVVIA_ZION_CMD.bat** conserva il vecchio avvio guidato con CMD per eventuale regressione.

**Stato:** interfaccia verificabile tramite test automatici Windows, ma il funzionamento reale dell'integrazione hcom con Claude/Codex su ogni PC non è garantito. Se Codex segnala \`database is locked\` o un errore App Server, la finestra Errori mostrerà i dettagli e ZION si fermerà; non tenterà di eliminare o riparare automaticamente il database.

## Avvio su Windows (PowerShell) — alternativa manuale

Se preferisci i passaggi manuali, scarica ZION e verifica i requisiti:

~~~powershell
git clone https://github.com/MATRIXNEO23/ZION-orchestrator.git
cd ZION-orchestrator
py -3 zion.py doctor
~~~

Aprire **due terminali nella cartella del progetto da modificare** (per esempio LUMI), e avviare:

~~~powershell
# Primo terminale
hcom claude
# Secondo terminale
hcom codex
~~~

In un terzo terminale eseguire **hcom list** per leggere i nomi effettivi dei due agenti. Avviare ZION sostituendo i percorsi e i nomi:

~~~powershell
py -3 C:\percorso\ZION-orchestrator\zion.py run "Correggi il problema richiesto" --project C:\percorso\LUMI --claude NOME_CLAUDE --codex NOME_CODEX
~~~

Se i nomi corrispondono a claude e codex, le due opzioni per i nomi sono facoltative. I terminali degli agenti devono essere già avviati **nella stessa cartella di progetto**. `zion.py` non autentica o avvia automaticamente le due IA. L'installazione iniziale dei programmi mancanti e gestita separatamente da `INSTALLA_ZION.bat`.

Prova senza inviare messaggi:

~~~powershell
py -3 zion.py run "Controlla un piccolo problema" --project . --dry-run
~~~

Ultimo stato:

~~~powershell
py -3 zion.py status --project C:\percorso\LUMI
~~~

## Sicurezza e limiti

- Usare preferibilmente un **worktree o branch Git pulito**. ZION rifiuta modifiche preesistenti, a meno che non si specifichi consapevolmente --allow-dirty.
- Non esegue direttamente commit, push, deploy o cancellazioni; ordina alle IA di non farlo. **Le autorizzazioni reali dipendono dalle impostazioni delle due CLI.** Verificarle prima di usare il programma su progetti importanti.
- Salva rapporti e stato nella cartella del progetto **.zion/runs/**. È consigliato aggiungere .zion/ al gitignore del progetto e non pubblicare report contenenti informazioni riservate.
- hcom può rilevare quando un agente diventa inattivo, ma ciò non prova che abbia completato il compito: ZION aspetta un **rapporto JSON verificabile**. In sua assenza si ferma al timeout.
- Non esegue retry illimitati e non finge che un test sia riuscito.
- **Non ancora collaudato con due account autenticati su un PC Windows reale.** I test automatici verificano la logica Python, non le integrazioni live o le quote.

## Test

~~~powershell
py -3 -m unittest discover -s tests -v
~~~

## Appunti futuri (non attivi)

- [Agenti locali offline](docs/PROGETTO_AGENTI_LOCALI_FUTURO.md)
- [LUMI multimodello](docs/APPUNTI_LUMI_MULTIMODELLO.md)

Motore hcom: [aannoo/hcom](https://github.com/aannoo/hcom), licenza MIT. ZION usa la sua CLI come dipendenza esterna, senza copiare il codice sorgente.
