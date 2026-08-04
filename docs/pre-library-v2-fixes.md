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

## Branch und Commit

`fix/pre-library-v2-audit-bugs` basiert direkt auf `dev` und enthält genau
einen Commit: `c1fc84cd7`. Library-V2-Upgrade-Intent, CAS,
Candidate-Kind-Bindung und Autolink sind bewusst nicht enthalten. Der Commit
kann vor Library V2 gemerged oder einzeln cherry-gepickt werden.

## Verifikation

```text
pytest -q tests/imports tests/test_async_utils.py tests/test_chat_page.py \
  tests/test_torrent_client_adapters.py tests/test_usenet_client_adapters.py
```

Der Branch selbst ist geprüft: **866 Python-Tests** und der Import-UI-Test
bestanden; `git diff --check` ist sauber.
