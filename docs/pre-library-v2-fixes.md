# Vor Library V2 mergefähige Auditfixes

## Zweck

Diese Fixes betreffen bereits `dev` (`d0cb43db5b5bac4b1bdead061d3c05f81c9a26d0`)
und dürfen unabhängig von Library V2 gemerged werden:

- atomare, failure-safe File-Replacements;
- wahrheitsgemäße Auto-Import-Endzustände und exactly-once Approval;
- Manual-Import-Containment auf erlaubte Staging-/Download-Verzeichnisse;
- getrennte Blocking-I/O-Pools für Provider- und Download-Control-Aufrufe;
- robuste Chat- und Node-26-Testgates.

Auto Import existierte vor `library-overhaul`; seine Fixes sind ausdrücklich
nicht Library V2 zuzurechnen.

## Aktueller Commitstand

- `f20c7b5f3` ist ein isolierter Runtime-/Test-Commit und grundsätzlich einzeln
  cherry-pickbar.
- `778c19cf3` mischt gemeinsame Importfixes und Library-V2-Upgrade-Sicherheit,
  weil beide dieselben Hunks in `core/imports/pipeline.py` und `web_server.py`
  benötigen. Diesen Commit nicht vollständig auf `dev` cherry-picken.

## Noch auszuführende Branch-Arbeit

Von `dev` einen Branch `fix/pre-library-v2-audit-bugs` erstellen und dort
genau einen dokumentierten Commit bauen. Aus `778c19cf3` nur I01/I04–I06
übernehmen; Upgrade-Intent, Library-V2-CAS, Candidate-Kind-Bindung und
Library-V2-Autolink bleiben draußen. Danach mindestens ausführen:

```text
pytest -q tests/imports tests/test_async_utils.py tests/test_chat_page.py \
  tests/test_torrent_client_adapters.py tests/test_usenet_client_adapters.py
```

Der entsprechende Fokus ist auf `library-overhaul` grün: 781 Import-/Chat-
Tests und 86 Executor-/Adaptertests. Der neue `dev`-Branch muss trotzdem selbst
getestet werden, weil die gemischten Pipeline-Hunks selektiv übertragen werden.
