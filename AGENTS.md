# Arbeiten an ct4

Kurzfassung für Menschen und Agents. Ausführlich steht alles in `PLAN.md`.

## Was ct4 ist

Ein Fork von Cheetah3. Er soll drei Dinge zugleich sein: byte-genau
verträglich mit ct3, gut im Erzeugen von JSON, und prüfbar ohne die
Anwendung, für die eine Vorlage geschrieben ist.

## Alles laufen lassen

```
docker compose -f tests/docker/compose.yml run --rm tests
```

Das macht ruff, mypy, die Tests des Werkzeugs, die Testsuite von ct3,
`ct4 check` über alle Skins, die Aufgaben zur Diagnostik und den
Korpus-Prüfstand. Einzeln geht `... run --rm tests lint|unit|cheetah|
check|reach|evals|corpus`. Nach einer Änderung reicht `... run --rm
tests quick`: ruff, mypy, die Tests des Werkzeugs, `ct4 check` und die
Reichweite, in zwei Minuten. Alles zusammen dauert zehn und läuft
einmal vor dem Commit.

## Eine Vorlage prüfen

```
ct4 check pfad/zur/vorlage.tmpl --format=json
ct4 context pfad/zur/vorlage.tmpl        # was sie aus dem Kontext liest
ct4 reference --json                     # alle Direktiven und Einstellungen
ct4 mcp                                  # dasselbe als MCP-Server
ct4 render seite.tmpl --context aufzeichnung.json   # rendern ohne weewx
ct4 fixture capture --weewx ~/src/weewx --out fixtures   # aufzeichnen
```

`ct4 check` braucht die Anwendung nicht. Was weewx an Namen kennt, steht
angemeldet in `declarations/weewx.json`.

## Zwei Regeln, die über allem stehen

**Verträglichkeit wird gemessen, nicht behauptet.** Der Korpus in
`corpus/` hält fest, was ct3 tut. Jede Änderung an `Cheetah/` läuft
gegen ihn. Bleibt er nicht bei 100 Prozent, ist die Änderung falsch,
nicht der Korpus.

**Ein Falschbefund ist schlimmer als ein übersehener Fehler.** Er bringt
Leute dazu, das Werkzeug abzuschalten. Wo ct4 etwas nicht sicher weiß,
schweigt es.

## Wo was liegt

| | |
|---|---|
| `Cheetah/` | der Fork, Semantik wie ct3 |
| `ct4/` | alles Neue: Prüfstand, JSON-Modus, Werkzeuge |
| `corpus/` | der Vergleichskorpus und die Fixtures |
| `declarations/` | was Anwendungen an Namen anmelden |
| `tests/evals/` | Aufgaben zur Diagnostik |
| `examples/` | ein JSON-Skin für weewx |

## Stil

`Cheetah/` bleibt im Stil, den es hat: keine Annotationen, nichts
umformatieren, was nicht inhaltlich geändert wurde. `ct4/` ist neu und
durchgehend annotiert, mypy läuft dort strict. Zeilen bis 79 Zeichen,
überall.
