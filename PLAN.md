# Cheetah 4 (ct4) — Entwurfsplan

**Wer das nicht lesen muss:** wer ein weewx-Skin anpassen will. Dieses Dokument
begründet den Entwurf und richtet sich an Leute, die ct4 bauen oder darüber
entscheiden. Messwerte und verworfene Alternativen stehen hier, nicht in der
späteren Benutzerdokumentation.

Stand: 31-Aug-2026. Referenz ist Cheetah3 3.4.0.post5 (stable, 29-Nov-2025),
Repo-Stand `master` vom 24-Jul-2026.

---

## 1. Ausgangslage, gemessen

Alle Zahlen aus dem geklonten Repo und einer Installation von `ct3` unter
Python 3.14.3.

| Gegenstand | Wert |
|---|---|
| Python-Code im Paket `Cheetah` | 19.833 Zeilen |
| davon `Parser.py` / `Compiler.py` / `Template.py` | 2.790 / 2.185 / 2.060 |
| C-Erweiterungen | `_namemapper.c` 542, `_filters.c` 93, `_template.c` 52 |
| Direktiven | 49 |
| Compiler-Settings | 56 |
| Testmethoden | 622, davon 65 Testklassen allein in `SyntaxAndOutput.py` |
| `python_requires` | `>=2.7` |
| Build | `setup.py` + `setup.cfg`, kein `pyproject.toml` |
| Default-Filter | `RawOrEncodedUnicode`, also kein Escaping |
| Compile-Cache | `dict` im Prozess, nichts auf Platte |

Der Codegenerator erzeugt Python-Quelltext durch Stringkonkatenation. Es gibt
keine Zwischenstufe, die man analysieren könnte. Ein Platzhalter wird zu:

```python
_v = VFFSL(SL, "u.address.city", True)   # '$u.address.city' on line 5, col 23
if _v is not None: write(_filter(_v, rawExpr='$u.address.city'))
```

`VFFSL` ist `valueFromFrameOrSearchList`. Der Pfad ist ein **String**, der zur
Laufzeit aufgelöst wird, unter anderem über `inspect.stack()`. Das dritte
Argument ist das Autocalling.

Zwei Fehlermeldungen, so wie sie heute herauskommen:

```
NotFound : cannot find 'nmae'
```

```
ParseError :
Some #directives are missing their corresponding #end ___ tag: for
Line 2, column 4
```

Der Parser-Fehler ist brauchbar. Der Laufzeitfehler nennt weder Datei noch
Zeile noch Kandidaten. Das ist die häufigste Fehlerklasse beim Arbeiten an
Skins.

Ein Durchsatzvergleich, 200 Zeilen Tabelle, C-NameMapper aktiv:

|          | ct3      | jinja2   |
|----------|----------|----------|
| Render   | 0,448 ms | 0,232 ms |
| Compile  | 0,22 ms  | 0,29 ms  |

Cheetah ist beim Rendern Faktor 1,9 langsamer und beim Kompilieren schneller.
Der Abstand beim Rendern kommt fast vollständig aus der Stringauflösung im
NameMapper.

---

## 2. Schwerpunkt: JSON

ct3 kann genau eine Sache: Bytes schreiben. Für HTML reicht das, weil HTML
fehlertolerant ist. Für JSON reicht es nicht, weil JSON es nicht ist.

Jeder JSON-Fehler, den man in Cheetah-Skins findet, folgt aus derselben
Ursache. Die Engine weiss nicht, dass sie JSON schreibt:

- **Kommas.** Eine `#for`-Schleife über Datensätze erzeugt entweder ein Komma
  zu viel am Ende oder eines zu wenig. Der Autor löst das mit einem Zähler oder
  mit `#if`. Das ist Handarbeit an jeder Schleife.
- **Typen.** `$day.outTemp.max` liefert einen formatierten String. Im JSON
  landet `"12.3"`, nicht `12.3`. Wer `.raw` benutzt, umgeht die Formatierung
  und verliert die Rundung.
- **Null.** Ein fehlender Messwert wird zu `None` und damit im Text zu `None`,
  nicht zu `null`.
- **Escaping.** Ein Anführungszeichen oder ein Zeilenumbruch im Stationsnamen
  zerlegt die Datei.

Dass weewx `.json`, `.raw` und `$jsonize()` an seine Helper-Objekte angebaut
hat, ist der Beleg. Die Aufgabe wurde in die Domänenschicht verschoben, weil
die Engine sie nicht annehmen konnte.

**Die These von ct4:** ein JSON-Template beschreibt keine Zeichenfolge, sondern
eine Struktur. Der Compiler baut Werte, ein Serialisierer schreibt sie. Damit
sind Kommas, Escaping, Typen und `null` keine Autorenprobleme mehr, sondern
durch Konstruktion erledigt.

Das ist der Punkt, an dem ct4 besser sein soll als alles andere. Jinja2 hat
`tojson` als Filter, aber die Kommas schreibt man dort auch von Hand. Mako
ebenso. Kein verbreiteter Template-Motor parst sein Zielformat.

HTML, PHP und Code bleiben unterstützt und werden besser. Sie sind aber nicht
der Punkt, an dem ct4 sich messen lassen will.

---

## 3. Leitentscheidungen

Fünf Weichen, alles Weitere folgt daraus.

### W1 — Fork, nicht Neuschreiben

Die Semantik von ct3 steckt zu grossen Teilen nicht im Entwurf, sondern in
Details: 56 Compiler-Settings, das Whitespace-Gobbling, die Reihenfolge der
searchList, das Autocalling, das Verhalten bei `None`. Eine Neuimplementierung
aus der Spezifikation heraus wird nie voll kompatibel, weil die Spezifikation
das nicht hergibt.

Also: Fork mit Historie, Verhalten einfrieren, dann von innen umbauen.

Gemacht am 31-Aug-2026, Branch `ct4` von `upstream/master`, 2564 Commits.
Basis ist nicht das Tag 3.4.0.post5, sondern der Stand danach: dazwischen liegen
fünf Commits, darunter „Drop support for Python 3.4 and 3.5" und „Stop using
load_module() in compat". Beide gehen in dieselbe Richtung wie P1. Referenz für
den Vergleich bleibt trotzdem das ausgelieferte 3.4.0.post5, weil das ist, was
weewx-Anwender installieren.

### W2 — Drei Ausgabemodi, ein Compiler

| Modus    | Zweck                                       | Kompatibilität                |
|----------|---------------------------------------------|-------------------------------|
| `text`   | ct3-Verhalten, byteweise                    | byte-identisch, das ist die Zusage |
| `json`   | Struktur bauen, dann serialisieren          | neu                           |
| `markup` | HTML, XML, PHP mit kontextabhängigem Escaping | opt-in                      |

Der Modus kommt aus der Dateiendung oder aus einer `#mode`-Direktive, nicht aus
einer globalen Einstellung. Ein Skin enthält beides.

Ein Compiler, der Modus ist ein Flag im Kontext. Kein zweiter Codepfad, sonst
pflegt man zwei Semantiken und keine davon richtig.

### W3 — Kompatibilität wird gemessen, nicht behauptet

Ein differenzieller Prüfstand: dasselbe Template, derselbe Kontext, ct3 und ct4
nebeneinander, Bytevergleich. Das ist das Abnahmekriterium, nicht eine Liste
abgehakter Merkmale.

Der Korpus kennt zwei Arten von Fall:

| Art | Verglichen wird | Braucht einen Kontext |
|---|---|---|
| `render` | die Ausgabe | ja |
| `compile` | der erzeugte Modulcode | nein |

Die zweite Art ist der Grund, warum fremde Skins überhaupt in den Korpus
passen. Ein weewx-Skin lässt sich nicht rendern, ohne weewx samt Datenbank zu
starten; sein Kontext ist eine laufende Anwendung. Übersetzen lässt er sich
sehr wohl, und damit wird genau das geprüft, was P4 ersetzen will.

Ein Fall muss sich aus seiner eigenen Zeile rekonstruieren lassen. Der Ernter
prüft das, indem er jeden Fall sofort ein zweites Mal rendert, in einem leeren
Arbeitsverzeichnis. Wer das nicht übersteht, weil er eine Datei daneben braucht
oder `os.environ` liest, fällt raus und wird gezählt.

### W4 — Domänenwissen wird angemeldet, nicht eingebaut

Der Kern von ct4 lernt nicht, was ein Messwert, eine Einheit oder ein Aggregat
ist. Sobald die Engine Einheiten kennt, kennt sie die falschen, und ein
Template-Motor, der eine Domäne kennt, ist für jede andere unbrauchbar.

Das heisst nicht, dass die Anwendung alles selbst machen muss. Es gibt eine
Registry, in die sie ihr Wissen einträgt: welche Namen es gibt, welchen Typ sie
liefern, wie sie serialisiert werden, wohin das Ergebnis geht (Abschnitt 6).

Die Aggregation selbst bleibt in weewx. Das Wissen darüber wandert nach ct4.

### W5 — Determinismus ist ein Merkmal, kein Nebeneffekt

Gleiche Eingabe, gleiche Bytes. Keine Zeitstempel im Artefakt, stabile
Schlüsselreihenfolge, stabile Zahldarstellung. ct3 schreibt heute
`__CHEETAH_genTime__` in jedes generierte Modul.

Der praktische Nutzen ist bei weewx unmittelbar: wenn sich die Bytes nicht
ändern, überträgt rsync oder FTP die Datei nicht.

---

## 4. Der JSON-Modus

### Wie ein Template aussieht

Ein JSON-Template ist ein JSON-Dokument mit Löchern. Der Parser kennt die
JSON-Grammatik und weiss deshalb jederzeit, ob er an einer Wert-, Schlüssel-
oder Elementposition steht.

```
{
  "station":   $station.location,
  "generated": $current.dateTime.raw,

  "day": {
    "outTemp": {
      "min": $day.outTemp.min,
      "max": $day.outTemp.max
    },
    #if $day.rain.sum.raw
    "rain": $day.rain.sum,
    #end if
    "records": [
      #for $r in $day.records
      { "t": $r.dateTime.raw, "v": $r.outTemp }
      #end for
    ]
  }
}
```

Zu beachten: nach dem letzten Schleifendurchlauf steht kein Komma, und nach
`"rain"` steht auch dann keins, wenn `#if` nicht greift. Der Autor schreibt die
Kommas so, wie sie im Normalfall stehen. Um den Rest kümmert sich der Compiler,
weil er ein Array baut und keine Zeichenfolge.

`#` und `$` sind in JSON frei. Es gibt keine Mehrdeutigkeit, und die Syntax
bleibt die von Cheetah.

### Was die Engine zusichert

| Problem in ct3 | Zusicherung in ct4 |
|---|---|
| Komma zu viel oder zu wenig | strukturell unmöglich |
| `"12.3"` statt `12.3` | Zahlen bleiben Zahlen |
| `None` im Output | `null` |
| Anführungszeichen zerlegen die Datei | `json.dumps` escaped, nicht der Autor |
| Rundung über Formatstrings, die Strings erzeugen | `precision` als Zahl, Ergebnis bleibt Zahl |
| Fehlender Wert mal `null`, mal `""`, je nach Template | Politik pro Feld: `omit`, `null`, `error` |
| Grosse Serien belegen Speicher als String | Serialisierung streamt |

### Präzision

Formatstrings sind für JSON die falsche Abstraktion, weil sie aus Zahlen
Strings machen. ct4 kennt `precision` als Nachkommastellen:

```
#precision default = 2
#precision max = 1

"max": $day.outTemp.max          ## 12.3
"max": $day.outTemp.max @ 3      ## 12.345, lokal überschrieben
```

`#precision` schlüsselt auf den **Feldnamen der Ausgabe**, nicht auf den
Messwerttyp. Das ist keine Bequemlichkeit, sondern W4: die Engine kennt keine
Messwerttypen. Wer die Rundung am Messwert festmachen will, liefert sie über
`Ct4Value.precision` aus der Anwendung. Bei weewx tut das der Typ-Adapter, und
zwar aus den Formatstrings, die der Skin ohnehin hat.

Es gilt: Angabe im Template vor Angabe der Anwendung vor `default`.

Die Rundung passiert vor der Serialisierung, auf dem Zahlwert. Das Ergebnis ist
plattformunabhängig gleich.

### Wert oder Text

An einer Wertposition ist ein Platzhalter ein **Wert** und geht durch
`__ct4_value__`. In einer Zeichenkette ist er **Text** und behält die
Formatierung, die das Objekt selbst mitbringt.

```
"max": $day.outTemp.max     ## 12.3
"text": "$day.outTemp.max"  ## "12.3 °C"
```

Der Unterschied ist Absicht. Wer eine Zahl will, schreibt sie an eine
Wertposition; wer die Anzeige will, schreibt sie in eine Zeichenkette.

### Serien

Das Layout einer Zeitreihe ist eine Serialisierungsentscheidung, keine
Schleife. Parallele Arrays gegen Paare gegen Datensätze ist in ct3 etwas, das
jeder Skin anders löst und das man dem Leser erklären muss.

```
"outTemp": #series($day.outTemp.series, layout="columns", precision=1, gaps="null")
```

| `layout`  | Ergebnis |
|-----------|----------|
| `columns` | `{"start": [...], "stop": [...], "value": [...]}` |
| `pairs`   | `[[t, v], [t, v], ...]` |
| `records` | `[{"t": ..., "v": ...}, ...]` |

`gaps` steuert Lücken: `null`, `omit` oder `interpolate`. Der Serialisierer
läuft über einen Generator, der Speicher bleibt konstant.

### Schema

```
#schema "station-day.schema.json"
```

Damit wird das Zielformat prüfbar:

- `ct4 check` prüft statisch, was ohne Daten prüfbar ist: Pflichtfelder
  vorhanden, Literaltypen passend, keine unbekannten Felder bei
  `additionalProperties: false`.
- `ct4 render --validate` prüft das Ergebnis zur Laufzeit.

Das ist zugleich der stärkste Hebel für die Arbeit durch KI-Agents
(Abschnitt 11): das Zielformat ist deklariert und maschinell prüfbar, statt im
Kopf des Skin-Autors zu stehen.

---

## 5. HTML, PHP und Code

Kein Schwerpunkt, aber kein Stiefkind.

- **Kontextabhängiges Escaping** im `markup`-Modus. Der Lexer weiss, ob ein
  Platzhalter im Textknoten, im Attribut, in einer URL oder im `<script>` steht,
  und escaped entsprechend. Das ist der TODO-Eintrag „Smart HTML filter", der
  seit Cheetah 2.0 offen ist.
- **`__html__`-Protokoll** wie bei markupsafe, damit vorbereitetes Markup nicht
  doppelt escaped wird.
- Für PHP und andere Sprachen wird kein Escaping geraten. Explizite Filter, aber
  mit Warnung, wenn ein Platzhalter ungefiltert in eine Position gerät, die
  offensichtlich Quoting braucht.

Im `text`-Modus bleibt alles wie in ct3, also ohne Escaping. Alles andere wäre
ein stiller Bruch.

---

## 6. Die Adapter-Schicht

Die Frage ist nicht, was die Engine könnte, sondern was sie soll. Und wie eine
Anwendung Aufgaben abgibt, die sie bisher selbst tragen musste.

**Engine:** Struktur, Serialisierung, Typen, Präzision, Lückenbehandlung,
Streaming, Determinismus, Reihenfolge, Dateiausgabe.

**Anwendung (weewx):** was ein Messwert ist, welche Einheit er hat, wie
aggregiert wird, was ein Archivintervall ist.

Verbunden wird das nicht durch Sonderfälle im Compiler, sondern durch eine
Registry, in die die Anwendung ihr Wissen einträgt.

### Der Ansatz steckt in ct3 schon drin, halb fertig

`Parser.py` kennt das Setting `macroDirectives`: eine Abbildung von Name auf
Callable, die der Parser als echte Direktiven einhängt. Die einzige
mitgelieferte Anwendung ist `#i18n`, und ihr Docstring sagt selbst „This is just
a stub at this time".

Drei Gründe, warum darauf nie jemand etwas gebaut hat:

- Der Mechanismus arbeitet **textuell**. Das Plugin gibt Quelltext zurück, der
  neu geparst wird. Positionen, Fehlermeldungen und jede spätere Formatierung
  gehen dabei verloren.
- Es leckt Parser-Interna. Ein Makro bekommt `startPos`, `endPos`,
  `EOLCharsInShortForm` durchgereicht.
- Es ist ein **Compiler-Setting**. Man muss es durch jede
  `Template`-Konstruktion tragen, und ein Werkzeug, das nur die Datei ansieht,
  findet es nie.

ct4 muss also keine Plugin-Idee erfinden. Es muss eine zu Ende bauen.

### Das Prinzip: anmelden und ausführen

Ein Plugin, das nur **ausführt**, bringt für ct4 fast nichts. Der Wert von
`ct4 check`, dem Sprachserver und der Agent-Schleife hängt daran, Dinge zu
wissen, ohne sie laufen zu lassen. Genau das ist der Grund, warum ct3s Makros
folgenlos blieben.

Also liefert dieselbe Klasse beides: eine statische Deklaration und eine
Laufzeit-Implementierung.

### Vier Anschlussstellen

| Anschluss | Was angemeldet wird | Was weewx damit abgeben kann |
|---|---|---|
| Namespace-Provider | Wurzeln, Felder, Typen, Präzision | macht statische Prüfung überhaupt erst möglich |
| Typ-Adapter | `__ct4_value__`, `__ct4_json__` | `$jsonize()`, `.raw`, Formatstrings für JSON |
| Output-Sink | wohin das Ergebnis geht | eigenes Dateischreiben, mtime-Vergleich, Upload-Anbindung |
| Direktiven-Plugin | AST-Knoten, nicht Textersetzung | eigene Konstrukte, ohne Positionsverlust |

#### Namespace-Provider

```python
class Ct4Namespace(Protocol):
    def declare(self) -> NamespaceDecl: ...  # statisch, ohne Datenbank
    def resolve(self, path, ctx): ...        # zur Laufzeit
```

weewx meldet an: es gibt `$day`, `$month`, `$current`, `$span`; darauf gibt es
`max`, `min`, `avg`, `sum`, `maxtime`; `max` liefert eine Zahl mit einer
Präzision aus der Skin-Konfiguration.

Danach kann `ct4 check` melden, dass `$day.outTemp.mx` nicht existiert und `max`
gemeint war. Ohne weewx, ohne Datenbank, in Millisekunden. Das ist die Stelle,
an der die Anmeldung den ganzen Werkzeugkasten aus Abschnitt 11 trägt.

Die Aggregation selbst wandert nicht. Das SQL bleibt in weewx.

#### Typ-Adapter

```python
@dataclass(frozen=True)
class Ct4Value:
    value: object          # der nackte Wert, z.B. float oder None
    precision: int | None  # Vorschlag für Nachkommastellen
    label: str | None      # z.B. "°C", nur für den Textmodus

class SupportsCt4Value(Protocol):
    def __ct4_value__(self) -> Ct4Value: ...

class SupportsCt4Json(Protocol):
    def __ct4_json__(self) -> object: ...   # volle Kontrolle über die Struktur
```

weewx' `ValueHelper` implementiert `__ct4_value__`. Danach funktioniert
`$day.outTemp.max` in JSON, in HTML und in Text richtig, ohne dass irgendwo
`$jsonize` oder `.raw` steht.

Steht als `ct4/plugins/weewx_adapter.py` und ist das erste Plugin. Es rechnet
nichts: es liest die Nachkommastellen aus den Formatstrings, die der Skin
ohnehin hat, und reicht den Rohwert durch. Solange `ct4-weewx` kein eigenes
Paket ist, hängt es die Methode nachträglich an die Klasse. Das ist der
Zustand, nicht das Ziel.

#### Output-Sink

Heute rendert die Engine in einen String, und der Aufrufer schreibt die Datei.
Deshalb bekommt niemand inhaltsbasiertes Schreiben geschenkt (Abschnitt 7). Mit
einem Sink trägt die Anwendung ein, wohin das Ergebnis geht, und bekommt
atomares Schreiben, Hash-Vergleich und den Upload-Anschluss aus der Engine.

#### Direktiven-Plugins

Der Nachfolger von `macroDirectives`, aber auf AST-Ebene. Das Plugin bekommt
geparste Argumente und gibt AST-Knoten zurück, keine Quelltext-Schnipsel. Damit
bleiben Positionen, Fehlermeldungen und der Formatierer intakt.

Das setzt den neuen Compiler-Kern voraus und kann deshalb erst nach P4 kommen.
Die anderen drei Anschlüsse brauchen ihn nicht.

### Registrierung über Entry Points

```toml
[project.entry-points."ct4.plugins"]
weewx = "weewx.ct4:plugin"
```

Nicht als Compiler-Setting. So findet ct4 das Plugin über die installierte
Umgebung, und `ct4 check` findet es auch, ohne dass die Anwendung läuft.

### Was das kostet

- **Portabilität.** Ein Template, das ein Plugin braucht, läuft nur dort, wo es
  installiert ist. Gegenmassnahme: `#requires` im Template und eine Meldung, die
  den Paketnamen nennt, statt „unknown directive".
- **Versionierte Zusage.** Das Interface trägt eine Version. ct4 lehnt
  Inkompatibles ab, statt zu raten.
- **Wildwuchs.** Wenn jedes Paket Syntax erfinden darf, sieht bald kein Template
  mehr wie Cheetah aus, und weder Mensch noch Agent kennt die Sprache noch.

Daraus die Regel: **Plugins dürfen alles beitragen ausser neuer Syntax.**
Namespaces, Typen, Sinks und Filter kommen automatisch über den Entry Point.
Neue Direktiven brauchen einen expliziten Eintrag in `ct4.toml`. Dann steht in
einer Datei, welche Sprache dieses Projekt spricht, und man kann sie nachlesen.

### Verworfen

Eine **eingebaute** Aggregatsprache (`$sum`, `$avg`, `$resample` im Kern). weewx
hat das bereits und macht es richtig, inklusive Einheiten. Eine zweite,
schlechtere Implementierung im Template-Motor wäre eine Fehlerquelle ohne
Gegenwert.

Angemeldete Aggregation über einen Namespace-Provider ist etwas anderes und
ausdrücklich vorgesehen. Der Unterschied: die Engine rechnet nicht, sie weiss
nur, was es gibt.

Was die Engine selbst an Auswertung mitbringt, bleibt schmal und
serialisierungsnah:

- Projektion und Filterung über Sequenzen im `#series`-Ausdruck
- Lücken- und Null-Behandlung
- deterministische Zahldarstellung, inklusive `Decimal`-Pfad, wo Rundung
  buchhalterisch sein muss

---

## 7. Automatisierung

Der Anwendungsfall ist ein Prozess, der alle paar Minuten Dateien erzeugt und
hochlädt. Daran ist der Entwurf ausgerichtet.

- **Byte-Determinismus.** Keine Zeitstempel im Artefakt, Schlüsselreihenfolge
  ist die des Templates, Zahldarstellung ist festgelegt.
- **Inhaltsbasiertes Schreiben.** Erst in eine temporäre Datei, dann Hash
  vergleichen, nur bei Unterschied ersetzen. Unveränderte Dateien behalten ihre
  mtime, und rsync oder FTP überträgt sie nicht.
- **Persistenter Compile-Cache.** ct3 cached kompilierte Templates in einem
  `dict` im Prozess. Ein Daemon zahlt das einmal, jeder frische Prozess zahlt es
  voll. Bei `weectl report run` und in Agent-Schleifen ist das der dominierende
  Posten. ct4 legt den Cache mit Hash-Schlüssel auf Platte.
- **Inkrementelle Erzeugung.** Abhängigkeitsgraph aus Template, Includes,
  Vererbung und benutzten Kontextschlüsseln. Nur erzeugen, was betroffen ist.
- **Batch-CLI.** `ct4 build manifest.toml -j4`, parallel, mit definierten
  Exit-Codes und einem JSON-Bericht, der sich in Cron und systemd auswerten
  lässt.

---

## 8. Kompatibilität: was das heisst

Voll kompatibel zerfällt in vier Ebenen mit je eigenem Prüfverfahren.

|    | Was | Prüfung |
|----|-----|---------|
| K1 | Ausgabe-Äquivalenz | Bytevergleich ct3 gegen ct4 über den Korpus |
| K2 | API-Äquivalenz: `from Cheetah.Template import Template`, `searchList`, `filter`, `filtersLib`, `.respond()` | Signatur-Schnappschuss aus ct3, Abgleich per Introspektion |
| K3 | Artefakt-Äquivalenz: von ct3 vorkompilierte `.py` laufen unter ct4 | Laufzeitsymbole `VFFSL`, `VFSL`, `VFN`, `DummyTransaction` bleiben |
| K4 | CLI-Äquivalenz: `cheetah compile`, `fill`, Exit-Codes, Dateilayout | Abnahmetests gegen ct3-Verhalten |

**Das eine Abnahmekriterium, das zählt:** weewx läuft mit unverändertem Code
und unveränderten Skins gegen ct4 und erzeugt byte-identische Ausgabe.

Bewusst nicht kompatibel: Python 2. Die Kompatibilitätsschicht (`compat.py`,
`try: import builtins except ImportError`) zieht sich durch den ganzen Code und
blockiert jede Modernisierung. Untergrenze wird Python 3.10.

**Paketname.** K2 verlangt, dass ct4 den Importnamen `Cheetah` liefert. Sonst
muss jeder Anwender seinen Code ändern, und dann ist es keine Kompatibilität
mehr. PyPI-Name wird `ct4`. Beim Import wird geprüft, ob ct3 danebensteht, und
mit einer klaren Meldung abgebrochen, statt still zu mischen.

---

## 9. Was weewx davon hat

Abschnitt 8 ist die Untergrenze. Er sagt nur, dass nichts kaputtgeht. Der Gewinn
kommt gestaffelt, und nicht alles davon ist umsonst.

### Stufe 1: sofort, niemand ändert etwas

Nur ct3 durch ct4 ersetzen, weewx-Code und Skins unverändert.

- **Fehlermeldungen.** Der grösste Alltagsgewinn, und er kostet niemanden etwas.
  Die Position steht heute schon im generierten Code
  (`# '$day.outTemp.max' on line 12, col 18`), sie wird nur nicht in den Fehler
  gereicht. Aus `NotFound: cannot find 'mx'` wird Datei, Zeile, Spalte und die
  Liste der Felder, die es auf dem Objekt tatsächlich gibt.
- **Tracebacks zeigen ins Template**, nicht in generiertes Python.
- **`weectl report run` wird schneller** durch den Compile-Cache auf Platte. Der
  Daemon merkt wenig, weil er den Prozess-Cache hat. Jeder manuelle Lauf merkt
  es.
- **Aktuelle Wheels**, Python 3.13 und 3.14, arm64, free-threading.
  Betriebsvorteil, kein Merkmal.

### Stufe 2: eine Änderung in weewx, einmalig

- **`ValueHelper.__ct4_value__` implementieren.** Danach liefert
  `$day.outTemp.max` je nach Modus von selbst das Richtige. Kein `.raw`, kein
  `$jsonize`, kein Formatstring, der aus einer Zahl einen String macht.
- **Output-Sink eintragen.** Die Datei schreibt heute der `CheetahGenerator`,
  nicht die Engine. Inhaltsbasiertes Schreiben kommt also nicht gratis. Mit
  einem Sink (Abschnitt 6) fällt der FTP- und rsync-Verkehr auf das, was sich
  wirklich geändert hat.
- **Namespace-Provider anmelden.** Erst damit kann `ct4 check` Tippfehler in
  Skins finden, ohne weewx zu starten.

Der Aufwand ist bei allen dreien klein und einmalig. Der Nutzen ist es nicht.

### Stufe 3: Skin umstellen, pro Datei

Erst hier greift der JSON-Modus. Eine `.json.tmpl` umstellen, dann sind
Komma-Akrobatik, `"12.3"`-Fallen und `None` im Output weg. `#series` ersetzt die
handgebaute Schleife. `#schema` macht das Format in CI prüfbar.

Opt-in und dateiweise. Ein Skin darf halb umgestellt sein.

### Für die Arbeit an Skins

Braucht nur ct4, keine weewx-Änderung:

```
ct4 fixture capture --from-weewx > fixtures/seasons-day.json
ct4 render index.json.tmpl --context fixtures/seasons-day.json
```

Skin-Entwicklung ohne laufende Wetterstation, ohne Datenbank, in Millisekunden
statt eines Report-Laufs. Das verändert die Schleife für Menschen und für Agents
gleichermassen.

### Was nicht besser wird

Die weewx-Tag-Syntax bleibt, wie sie ist. `$day.outTemp.max` bleibt
`$day.outTemp.max`. ct4 macht weewx nicht schöner. Es macht das Werkzeug
darunter und das Werkzeug darum herum besser.

---

## 10. Architektur

Heute:

```
Quelle -> Parser (zeichenweise, erzeugt direkt Codestrings) -> Python-Quelltext -> exec
```

Es gibt keine Zwischenstufe. Jedes Werkzeug, das ein Template verstehen will,
müsste den Parser nachbauen. Das ist der Grund, warum es für Cheetah keine
Formatierer, keine Sprachserver und keine Linter gibt.

Ziel:

```
Quelle -> Lexer -> CST (verlustfrei, mit Spans) -> AST -> Analysen -> Codegen -> Python-AST -> compile()
                    |                               |
               Formatter, LSP              Typprüfung, Schema, Lint
```

Vier Punkte, auf die es ankommt:

1. **CST verlustfrei.** Jeder Leerraum, jeder Kommentar, exakte Bytespannen.
   Ohne das gibt es keinen Formatierer und keine sicheren strukturellen Edits.
2. **Codegen über das `ast`-Modul** statt über Stringkonkatenation. Damit
   stimmen die Zeilennummern, und Quelltextzuordnung wird möglich.
3. **Source Maps.** Ein Traceback zeigt auf `index.json.tmpl:17`, nicht auf
   `_generated.py:243`.
4. **Der JSON-Parser ist klein.** JSON hat eine Grammatik, freier Text hat
   keine. Deshalb kann der JSON-Modus früh kommen, unabhängig vom Umbau des
   Textparsers.

Der NameMapper bleibt als C-Fast-Path für den `text`-Modus. Im `json`- und im
`strict`-Modus erzeugt der Codegen direkten Attributzugriff statt
Stringauflösung. Das ist schneller und statisch analysierbar.

---

## 11. Die Werkzeug- und KI-Schicht

Ein Agent, der ein Template ändert, braucht drei Dinge, die ct3 nicht hat: zu
wissen, welche Variablen es gibt; eine Fehlermeldung, aus der die Korrektur
folgt; und einen Weg, ohne die ganze Anwendung zu prüfen.

### Kontext-Fixtures, der wichtigste Einzelpunkt

Heute kann niemand ein weewx-Template prüfen, ohne weewx samt Datenbank laufen
zu lassen. Für einen Agent heisst das: keine Rückmeldung, also raten.

```
ct4 fixture capture --from-weewx > fixtures/seasons-day.json
ct4 render index.json.tmpl --context fixtures/seasons-day.json
```

Einmal einen echten Kontext einfrieren, danach läuft die Schleife offline, in
Millisekunden, ohne Wetterstation. Das ist der Unterschied zwischen einem Agent,
der prüfen kann, und einem, der Text produziert.

### Diagnostik als Datenstruktur

Jede Meldung trägt Code, Schweregrad, Datei, Bytespanne, Text, Erklärung und
Korrekturvorschläge mit konkreten Edits.

```
ct4 check --format=json
ct4 check --format=sarif      # für CI-Annotationen
```

Fehlercodes sind stabil (`CT4001` und so weiter), damit man über sie reden kann.
Aus `NotFound: cannot find 'nmae'` wird:

```
CT4103  index.json.tmpl:12:18
  Unbekanntes Feld 'mx' auf $day.outTemp
  Verfügbar: max, min, avg, sum, count, maxtime, mintime
  Meinten Sie 'max'?
```

### Weitere Werkzeuge

| Kommando | Zweck |
|---|---|
| `ct4 ast --json` | dokumentierter, versionierter AST für strukturelle Edits |
| `ct4 fmt` | idempotenter Formatierer, kanonische Form |
| `ct4 context --infer` | leitet aus dem Template ab, welche Kontextfelder es benutzt |
| `ct4 reference --json` | alle Direktiven und Settings maschinenlesbar, ohne Websuche |
| `ct4 diff-ct3` | Bytevergleich gegen ct3, Migrationshilfe |
| `ct4 mcp` | MCP-Server über stdio mit `check`, `render`, `context`, `schema`, `outline` |

Der Formatierer ist kein Kosmetikposten. Ohne kanonische Form erzeugt jeder
Agent-Edit Rauschen im Diff, und die Durchsicht durch einen Menschen bricht
zusammen.

Dazu ein Sprachserver und eine tree-sitter-Grammatik, beide auf demselben CST.
Und `AGENTS.md` im Wurzelverzeichnis.

### Evals statt Behauptung

AI ready ist prüfbar oder es ist Marketing. Eine Aufgabensammlung mit
automatisch prüfbarem Erfolg: eine Spalte einfügen, eine Variable umbenennen,
einen Schemafehler beheben, eine kaputte Schleife reparieren. Läuft in CI. Wenn
eine Fehlermeldung schlecht ist, fällt die Eval, und dann wird die Meldung
verbessert, nicht die Eval.

---

## 12. Performance

Ziel: Parität mit jinja2 im Textmodus, deutlich darüber im JSON-Modus, weil dort
die Serialisierung in C läuft statt als Stringkonkatenation in Python.

Hebel, nach erwarteter Wirkung:

1. Persistenter Compile-Cache, wirkt auf jeden frischen Prozess
2. Kein `inspect.stack()` je Platzhalter im `strict`- und `json`-Modus
3. `json.dumps` mit C-Encoder statt selbst gebauter Ausgabe
4. Ausgabe über Liste und `join` statt wiederholter `write`-Aufrufe

Benchmark-Suite im Repo und in CI, mit Schwelle. Eine Regression bricht den
Build.

---

## 13. Phasen

Reihenfolge und Abnahmekriterium, keine Wochenangaben. Über Kapazität weiss ich
nichts.

### P0 — Fundament und Beweislast

- Fork von ct3, Historie behalten
- Korpus aufbauen: ct3-Testsuite, weewx-Skins (mitgeliefert und Community)
- Prüfstand läuft in CI gegen ct3 als Referenz
- `ct4 fixture capture` für weewx

**Fertig, wenn:** Korpus mindestens 1.000 Fälle, ct4 stimmt auf 100 Prozent mit
ct3 überein.

Stand 31-Aug-2026: 1.772 Fälle, beide Seiten 100 Prozent, lokal unter Windows
und Python 3.14 wie im Container unter Linux und Python 3.13.

| Quelle | Fälle | Art |
|---|---|---|
| ct3-Testsuite | 1.628 | `render` |
| weewx: Seasons, Smartphone, Mobile, Standard, Beispiele, Test-Skins | 53 | `compile` |
| Belchertown, Belchertown New, weewx-wdc | 83 | `compile` |
| weewx-Seiten aus einem echten Report-Lauf | 8 | `render` |

Aus der Testsuite fehlen noch 62 Fälle: 40 wegen `macroDirectives` mit einer
Funktion darin, 14 wegen einer Datei neben der Vorlage, 6 wegen `os.environ`,
2 wegen einer nicht ablegbaren searchList.

`ct4 fixture capture` steht und liefert die letzte Zeile der Tabelle. Es hängt
sich in weewx' eigene Testsuite ein, zeichnet auf, was jede Vorlage aus dem
Kontext liest, und legt das als JSON ab. Danach rendert dieselbe Vorlage ohne
weewx und ohne Datenbank, byte-identisch. Was dabei aufzufallen hatte, steht in
Abschnitt 14.

### P1 — Infrastruktur, Semantik unverändert

- `pyproject.toml` (PEP 621), Python 3.10 als Untergrenze, Python-2-Code raus
- ruff, mypy strict für neuen Code, `py.typed` (PEP 561)
- pytest statt eigenem Runner, Testlauf in Docker mit fixierten Versionen, nicht
  als root, `--timeout=60`
- cibuildwheel für CPython 3.10 bis 3.14 einschliesslich 3.14t
  (free-threading), manylinux, musllinux, macOS, Windows, arm64
- Trusted Publishing zu PyPI

**Fertig, wenn:** Korpus weiter bei 100 Prozent, Wheels für alle Zielplattformen,
weewx läuft unverändert gegen ct4.

Stand 31-Aug-2026:

| | |
|---|---|
| Korpus | 1.772 Fälle, beide Seiten 100 Prozent |
| ct3-Testsuite gegen den Fork | 2.186 Tests, alle grün |
| weewx gegen ct3 und gegen ct4 | 80 erzeugte Seiten, byte-identisch |
| ruff, mypy strict auf `ct4` | ohne Befund |

Der Umbau des Build-Systems hat nebenbei einen Fehler gefunden: `SetupTools.py`
importierte `distutils` direkt, das es seit Python 3.12 nicht mehr gibt. Das ging
nur, weil setuptools einen Ersatz einhängt. Beide Dateien sind weg, alles steht
in `pyproject.toml`.

Von ruffs Vorgabe sind bewusst nur `E`, `W` und `F` aktiv, also das, was das
Projekt mit flake8 hatte. Die übrigen Familien meldeten 785 Stellen in fremdem
Code, darunter 273 Aufrufe von `%`-Formatierung. Das ist eine eigene
Entscheidung und gehört nicht in einen Umbau des Build-Systems.

Zwei Dinge, die P1 nicht erledigt hat: die Testsuite von ct3 läuft weiter über
ihren eigenen Runner, weil ihre Klassen nicht nach pytest-Muster heissen und
`install_eols()` die Zeilenende-Varianten erst zur Laufzeit erzeugt. Und
`py.typed` fehlt, weil `Cheetah` keine Annotationen hat; die Marke käme sonst
einer Zusage gleich, die niemand einlöst.

### P2 — JSON-Modus

Der Schwerpunkt, und er kommt früh, weil sein Parser klein ist und nicht auf den
Umbau des Textparsers wartet.

- JSON-Grammatik mit Löchern, Werteaufbau statt Byteausgabe
- `#precision`, `#series`, Null-Politik, Streaming
- Plugin-Registry über Entry Points, Typ-Adapter als erster Anschluss
- `ct4-weewx` als erstes Plugin: `ValueHelper` liefert `__ct4_value__`
- `#schema` mit statischer und Laufzeitprüfung

**Fertig, wenn:** ein weewx-Skin seine JSON-Dateien im neuen Modus erzeugt, das
Ergebnis gegen das Schema validiert, und die Dateien über mehrere Läufe
byte-stabil sind.

Stand 31-Aug-2026: erfüllt. `examples/weewx-json/day.json.tmpl` läuft gegen die
echte Report-Engine von weewx, hält sein Schema und liefert in zwei Läufen
dieselben Bytes. In der Vorlage steht kein `.raw`, kein `$jsonize` und keine
Komma-Akrobatik.

Der Entwurf, der das trägt: der JSON-Compiler deutet die Ausdrücke nicht. Er
erzeugt eine Cheetah-`#def`, die einen Bauplatz bedient, und **Cheetah
übersetzt die Ausdrücke**. Damit gelten im JSON-Modus dieselbe searchList,
dasselbe Autocalling und dieselbe Punktschreibweise wie im Textmodus. Ein
zweiter Ausdrucksparser wäre eine zweite Semantik, und eine davon wäre falsch.

Was noch fehlt: Streaming für grosse Serien, die Registry über Entry Points,
und ein Compiler, der statt einer Cheetah-`#def` direkt Python erzeugt. Der
Bauplatz hält die Struktur bisher ganz im Speicher.

### P3 — Werkzeuge und KI-Schicht

- Diagnostik-Objekte, Fehlercodes, JSON- und SARIF-Ausgabe
- Namespace-Provider mit `declare()`, Output-Sinks
- `ct4 check`, `context`, `reference`
- MCP-Server
- Eval-Suite in CI

**Fertig, wenn:** `ct4 check` einen Tippfehler in einem weewx-Skin findet, ohne
dass weewx läuft, und die Eval-Suite mit veröffentlichter Erfolgsquote in CI
durchläuft.

### P4 — Compiler-Kern

- Lexer, CST, AST, Codegen über `ast`
- Source Maps, deterministische Ausgabe, persistenter Compile-Cache
- Alter Compiler bleibt im Repo als Referenz für den Diff-Prüfstand
- Direktiven-Plugins auf AST-Ebene, Ablösung von `macroDirectives`
- Darauf aufbauend: `ct4 fmt`, `ct4 ast`, Sprachserver, tree-sitter

**Fertig, wenn:** Korpus byte-identisch mit dem neuen Backend, Tracebacks zeigen
in die Vorlage.

### P5 — `strict`-Modus und Performance

- Kein Autocalling, keine Frame-Auflösung, kontextabhängiges Escaping im
  `markup`-Modus, `async`-Rendering, Sandbox für Vorschau und Agent-Schleifen
- PEP-750-Interop: t-strings als Kontextwerte, die ihr Escaping mitbringen
- `ct4 migrate` schreibt Templates um und meldet jede Verhaltensänderung

**Fertig, wenn:** Legacy-Korpus weiter 100 Prozent, Migrationswerkzeug
verlustfrei über den Korpus, Benchmark-Ziele erreicht.

### P6 — Freigabe 4.0

Dokumentation, Migrationsleitfaden, CHEPs für die Sprachänderungen, falls
Anschluss an das Upstream-Projekt gewünscht ist.

---

## 14. Risiken

- **Kompatibilität ist ein Anspruch, kein Zustand.** Der Korpus deckt nie alles.
  Gegenmassnahme: jeder gemeldete Unterschied wird ein Korpusfall, bevor er
  behoben wird.
- **Der Kontext ist mehr als seine Werte.** Beim Aufzeichnen mussten vier Dinge
  mitgeschrieben werden, die man nicht erwartet: dass ein Wert eine gebundene
  Methode war (Cheetah ruft die von selbst auf und erkennt sie an `__func__`),
  dass ein Zugriff eine Ausnahme geworfen hat (ein Skin führt das absichtlich
  vor), welcher Ausgabefilter galt, und unter welchem Pfad ein weitergereichter
  Wert stand. Jedes dieser vier Stücke hat gefehlt und den Vergleich zum
  Scheitern gebracht. Es können weitere fehlen; ein Fixture ist erst richtig,
  wenn es byte-identisch reproduziert.
- **Eine gemessene Lücke: Stack-Frames.** Am 31-Aug-2026 gegen den Korpus
  gemessen, indem einzelne Compiler-Schalter umgelegt wurden. `useNameMapper`
  aus lässt 46 Prozent der Fälle gleich, `useAutocalling` aus 88 Prozent,
  `useFilters` aus 97 Prozent. `useStackFrames` aus lässt **alle** Fälle gleich.
  Der Korpus prüft die Frame-Auflösung also überhaupt nicht, und P5 will sie
  entfernen. Bevor das passiert, braucht es Fälle, die sie treffen.
- **`_namemapper.c` unter free-threading.** Die Anpassung an Python 3.14t ist
  echte Arbeit. Der reine Python-Pfad (`C_VERSION = False`) wird heute selten
  getestet und muss zuerst abgesichert werden.
- **Drei Modi sind drei Semantiken.** Gegenmassnahme: ein Compiler, Modus als
  Flag, jeder Testfall läuft in allen zutreffenden Modi mit erwartetem
  Unterschied.
- **Ökosystem-Spaltung.** ct3 und ct4 liefern beide `Cheetah`. Gegenmassnahme:
  Konflikterkennung beim Import, frühe Ansprache des Upstream-Projekts.
- **Über-Umfang.** Die Werkzeugschicht ist verlockend und gross. P3 baut deshalb
  bewusst nur das, was ohne den neuen Parser geht. Formatierer und Sprachserver
  warten auf P4, sonst baut man Werkzeuge auf einem Parser, den man gleich
  ersetzt.

---

## 15. Offene Entscheidungen

1. **Fork oder Upstream?** Empfehlung: eigener Fork, Änderungen aber so
   schneiden, dass sie als CHEP einreichbar bleiben.
2. ~~**Konkreter Bestand.**~~ Beantwortet: die mitgelieferten weewx-Skins,
   dazu Belchertown, Belchertown New und weewx-wdc. Alle im Korpus, Herkunft
   mit Commit in `corpus/skin-sources.json`.
3. **Importname.** `Cheetah` überschreiben (maximale Kompatibilität, keine
   Koexistenz) oder `cheetah4` mit Shim (Koexistenz, aber jeder Anwender muss
   etwas tun). Empfehlung: `Cheetah` überschreiben.
4. **Python-Untergrenze.** 3.10 (Empfehlung) oder 3.9, falls Distributionen das
   erzwingen.
5. **Wo lebt `ct4-weewx`?** Die Frage „Protokoll oder Paket" ist beantwortet:
   beides. Das Protokoll gehört in ct4, die Anbindung wird das erste Plugin.
   Offen bleibt, wer es pflegt. Im weewx-Repo folgt es Änderungen an
   `ValueHelper`, im ct4-Repo folgt es Änderungen am Interface. Empfehlung:
   anfangs im ct4-Repo, Übergabe an weewx, sobald das Interface steht.
6. **Dürfen Plugins Syntax beitragen?** Die Regel aus Abschnitt 6 sagt: nur mit
   explizitem Eintrag in `ct4.toml`. Die Alternative wäre, Direktiven-Plugins
   ganz zu verbieten und Plugins auf Namespaces, Typen und Sinks zu begrenzen.
   Das hielte die Sprache überall gleich, kostet aber Ausdruckskraft.
   Empfehlung: die `ct4.toml`-Regel.
