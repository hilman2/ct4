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

`gaps` steuert Lücken: `null`, `omit` oder `interpolate`.

Für grosse Reihen gibt es einen zweiten Weg, der schreibt statt zu sammeln.
Gemessen an 100.000 Punkten, beide Male mit einem Generator als Quelle:

| | Speicher | Zeit |
|---|---|---|
| sammelnd | 16,5 MB | 0,76 s |
| strömend | 0,2 MB | 1,36 s |

Also **93x weniger Speicher, dafür 1,8x langsamer**, und der Bedarf des zweiten
Wegs hängt nicht an der Länge der Reihe. Das ist ein Tausch, keine
Verbesserung: sammelnd bleibt die Vorgabe, strömend gibt es für den Fall, dass
der Speicher knapp ist. Auf einem Raspberry Pi ist er das.

Beide liefern **byte-identisch dasselbe**. Jeder Einzelwert geht durch
`json.dumps`, damit Escaping, Zahlformat und `null` sich nicht unterscheiden
können; die Trennzeichen kommen aus einer Quelle. Elf Testfälle prüfen die
Gleichheit, und der Lauf gegen echte weewx-Daten prüft sie noch einmal.

Nicht strömbar ist `layout="columns"`: dort steht der erste Wert jeder Spalte
neben dem letzten, und dafür muss die Reihe gesammelt werden.

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

### Gemessen am 31-Aug-2026

Eine Tabelle mit 200 Zeilen und drei Platzhaltern je Zeile, bestes von drei
Läufen:

| | je Render | zur Handschrift |
|---|---|---|
| von Hand geschrieben | 0,027 ms | 1x |
| jinja2 | 0,068 ms | 2,5x |
| ct4, wie ausgeliefert | 0,412 ms | 15x |

Wo die Zeit hingeht:

| | Anteil |
|---|---|
| NameMapper | 52 % |
| Ausgabefilter | 21 % |
| Rest (Schleife, `write`, Template-Maschinerie) | 26 % |

### Zwei der vier Hebel waren falsch

**Hebel 2 war verkehrt herum.** Der Plan wollte `inspect.stack()` je Platzhalter
sparen. Mit dem C-NameMapper gibt es kein `inspect.stack()`: der C-Code läuft
direkt über den Frame, und das ist der **schnelle** Weg. Schaltet man
`useStackFrames` ab, erzeugt der Compiler stattdessen

```python
_v = VFSL([locals()]+SL+[globals(), builtin], "r.name", True)
```

also eine neue Liste samt `locals()` und `globals()` bei **jedem** Platzhalter.
Gemessen: 0,393 ms mit Frames gegen 0,527 ms ohne. Abschalten kostet 34 Prozent.

**Hebel 4 gibt es schon.** `DummyResponse` sammelt in `_outputChunks` und fügt am
Ende mit `join` zusammen. Da ist nichts zu holen.

### Wo die Grenze wirklich liegt

Dieselbe Tabelle, aber der Rumpf einmal von Hand ausgeschrieben, so wie ein
Compiler ihn erzeugen könnte, der die Schleifenvariable kennt:

| | je Render | zu jinja2 |
|---|---|---|
| ct4, ganze Maschinerie | 0,402 ms | 5,9x |
| nur der Rumpf, mit NameMapper | 0,253 ms | 3,7x |
| nur der Rumpf, direkter Zugriff | 0,031 ms | **0,46x** |
| jinja2 | 0,068 ms | 1x |

Damit zerfällt der Aufwand in drei Teile:

| | ms | Anteil |
|---|---|---|
| Template-Maschinerie (`respond()`, Transaction, Filter, SearchList) | 0,149 | 37 % |
| NameMapper | 0,222 | 55 % |
| die eigentliche Arbeit | 0,031 | 8 % |

**Acht Prozent der Zeit ist Arbeit, zweiundneunzig sind Apparat.** Und die
0,031 ms sind bereits schneller als jinja2. Das Ziel liegt nicht über uns.

Daraus folgt, was für Parität nötig ist, und es sind zwei Dinge, nicht eines:

1. **Der NameMapper muss aus dem heissen Pfad.** Der Compiler schreibt `r.name`
   statt `VFFSL(SL,"r.name",True)`. Das kostet zwei ct3-Zusagen: die
   vereinheitlichte Punktschreibweise (`$r.name` findet auch `r["name"]`) und
   das Autocalling der ersten Komponente. Beides ist gemessen
   verhaltensändernd, festgehalten in `tests/unit/test_local_lookup.py`. Also
   nur im **Strict-Modus** aus W2, opt-in, nie als Voreinstellung.
2. **Die Maschinerie je Render muss weg.** jinja2 rendert über eine
   Modulfunktion mit Generator. ct4 baut je Aufruf ein Objekt, eine Transaction
   und einen Filter auf. Ohne diesen Posten bleiben 0,149 ms stehen, und damit
   ist Parität allein über Punkt 1 unerreichbar.

### Was davon umgesetzt ist

**0,402 auf 0,211 ms, Faktor 1,9. Von 5,9x auf 3,1x jinja2.** Byte-identisch,
keine Semantik geändert. Zwei Hebel:

**1. `resolveKnownLocals`, Voreinstellung an.** Der Compiler führt über
`indent()` und `dedent()` einen Stapel der Namen, die er selbst gebunden hat,
und lässt die Suche bei einem solchen Namen dort anfangen:

```python
_v = VFN({"r":r},"r.name",True)      # statt VFFSL(SL,"r.name",True)
```

Gemessen 0,400 gegen 0,246 ms, Faktor 1,62. VFFSL sieht ohnehin zuerst in die
Frame-Locals, die Abkürzung überspringt nur den Weg dorthin und behält jede
Regel, Autocalling der ersten Komponente eingeschlossen.

Dieselbe Buchführung braucht der Strict-Modus später ohnehin. Sie ist damit
nicht nur der schnelle Zwischenschritt, sondern die Voraussetzung für Punkt 1
oben.

**2. `DummyResponse.write` ist die `append`-Methode der Liste.** Ein erzeugtes
Template holt `write` einmal und ruft es für jede Konstante und jeden
Platzhalter: eine Seite mit 200 Zeilen kommt auf über 1600 Aufrufe. Als
Python-Methode kostet jeder einen Frame für einen einzigen `append`. Gemessen
0,052 gegen 0,023 ms für 1400 Schreibvorgänge, im vollen Render 12 Prozent.
Gesetzt nur, wo niemand `write` überschrieben hat.

Das Korpus vergleicht `compile`-Fälle weiter gegen ct3. Die eine beabsichtigte
Abweichung wird in `normalize_code` vor dem Vergleich zurückgerechnet, statt die
Baseline auf ct4 neu zu ziehen. Sonst würden die 136 fremden Skins nur noch
beweisen, dass ct4 mit sich selbst übereinstimmt.

### Was geprüft und verworfen ist

**Den Ausgabefilter beschleunigen.** Der Plan schätzte ihn auf 21 Prozent,
gemessen sind es 0,032 ms. Davon ist fast nichts zu holen: eine freie Funktion
statt der gebundenen Methode spart 0,004 ms, also 1,6 Prozent des Renders. Das
`**kw` in `Filter.filter` kann nicht weg, weil der `#errorCatcher`-Pfad
`_filter(_v, rawExpr='$x')` erzeugt, und genau das ist der teure Teil.

**Die Template-Konstruktion.** 0,001 ms je Render. Es gibt nichts zu holen.

### Was bleibt

| | ms | Anteil am Rest |
|---|---|---|
| NameMapper | 0,114 | 54 % |
| Filter | 0,032 | 15 % |
| `respond()`-Prolog und Transaction | 0,028 | 13 % |
| die eigentliche Arbeit | 0,035 | 17 % |

Der grosse Posten ist der NameMapper, und er ist im kompatiblen Modus nicht zu
holen. Damit sind die kompatiblen Hebel ausgeschöpft. Alles Weitere ist Punkt 1
oben, also Strict-Modus.

### Warum Python und nicht Rust

Die Antwort steht in der Tabelle oben: **die 0,031 ms sind reines CPython.**
Liegt der Boden in Python unter dem Ziel, ist die Sprache nicht der Engpass.
jinja2 ist selbst reines Python und belegt es.

Dazu drei Gründe, die auch für ein tieferes Ziel gelten:

- **Der heisse Pfad liegt in fremden Python-Objekten.**
  `$day.outTemp.max.formatted` ist kein Datenzugriff, sondern vier Aufrufe in
  `ValueHelper` und `TimespanBinder`, zwei davon über `__getattr__`. Eine
  Rust-Engine muss für jeden davon nach CPython zurück, und der Grenzübertritt
  kostet ungefähr so viel wie der Zugriff.
- **Cheetah kompiliert nach Python-Quelltext, und das ist Teil der Zusage.**
  `#set`, `#call` und jeder Ausdruck in `#if` sind Python und werden als Python
  eingebettet. Eine ct3-kompatible Rust-Engine müsste einen Python-Interpreter
  mitbringen.
- **Die Kompatibilität ist prüfbar, weil beide Seiten dieselbe Semantik
  ausführen.** `isInstanceOrClass` in `_namemapper.c` entscheidet das
  Autocalling über Sondierungen an `__func__`, `__code__`, `__self__` und der
  MRO. In Rust nachgebaut müsste jede dieser Feinheiten neu entdeckt werden, und
  jede Abweichung fiele erst bei einem Nutzer auf.

Dazu die Verteilung: weewx läuft auf Raspberry Pi, ARM und alten Debians. Reines
Python plus eine optionale C-Erweiterung installiert sich dort überall.

**Wo Rust sich lohnt:** die Serialisierung im JSON-Modus. Sobald die Werte
extrahiert sind, ist der Rest Daten zu Bytes ohne fremde Objekte im heissen
Pfad. Dafür gibt es `orjson`, und das ist Rust. Dann optional einbinden, nicht
selbst schreiben.

### Gegen ct3

Gemessen im Container gegen die installierte 3.4.0.post5, beides Python 3.13,
`tests/bench/render.py`:

| | ct3 | ct4 | |
|---|---|---|---|
| Text, blanke Objekte | 0,790 ms | 0,441 ms | **1,79x** |
| Text, Helper-Objekte wie weewx | 0,951 ms | 0,549 ms | **1,73x** |
| Text, JSON von Hand geschrieben | 1,120 ms | 0,740 ms | **1,51x** |
| Übersetzen | 0,693 ms | 0,724 ms | 0,96x |
| `json.dumps` als Kontrolle | 0,151 ms | 0,153 ms | 0,99x |

Übersetzen ist 4 Prozent langsamer: der Compiler ruft je `#for` einmal
`ast.parse`, um die Schleifenziele zu lesen. Das trifft nur den ersten Lauf, der
Compile-Cache deckt es ab.

### Der JSON-Modus, gemessen

Der Plan behauptete hier „deutlich über jinja2, weil die Serialisierung in C
läuft". Gemessen worden war das nie, und der erste Lauf sagte das Gegenteil: bei
500 Punkten brauchte der JSON-Modus über `#for` **2,30 ms**, die Textvorlage,
die er ersetzt, 0,73 ms. Er war dreimal langsamer als das, was er ablösen soll.

Der Grund war überall derselbe: Arbeit je Wert, die je Vorlage feststeht.

- Der Emitter sprach seinen eigenen Builder als `$B` an. `$B.item(x)` erzeugt
  `VFN(VFFSL(SL,"B",True),"item",False)`, also zwei Lookups je Aufruf, und eine
  Schleife macht vier Aufrufe je Element. `B` ist der Parameter der Definition,
  die der Emitter selbst schreibt. Blank geschrieben ist es ein direkter Zugriff.
- `Builder.prepare` ging für jedes `float` durch zwei fehlschlagende `getattr`
  und eine `Ct4Value`, um bei der Rundungsregel anzukommen. Eingebaute Skalare
  können keinen der beiden Haken tragen.
- Eine Serie zerlegte ihre Feldpfade je Element neu statt einmal.
- `json.dumps` legt bei jedem Aufruf mit eigenen Schlüsselwortargumenten einen
  neuen `JSONEncoder` an. Das Streamen rief es je Zeile.

Danach, dieselben 500 Punkte:

| | ms | zu Hand |
|---|---|---|
| von Hand, `json.dumps` | 0,130 | 1x |
| JSON-Modus, `#series` | 0,332 | 2,6x |
| JSON-Modus, `#for` | 0,534 | 4,1x |
| Textvorlage unter ct4 | 0,731 | 5,7x |
| JSON-Modus, streamend | 0,698 | 5,4x |

Davon sind 0,11 ms `json.dumps` selbst, und das ist C.

### Bei 100.000 Zeilen

Ein Jahr weewx-Archiv im Fünf-Minuten-Takt sind rund 105.000 Sätze. Zehn Werte
je Satz ist eine gewöhnliche Plot-Seite. `tests/bench/large.py`, Quelle als
Generator, damit gemessen wird, was die Maschine hält, nicht was der Aufrufer
übergibt:

| | Sekunden | Peak MB | Ausgabe MB |
|---|---|---|---|
| ct3, Textvorlage wie heute | 7,81 | 90,9 | 13,6 |
| ct4, dieselbe Textvorlage | 4,25 | 89,4 | 13,6 |
| ct4, JSON-Modus sammelnd | 2,86 | 58,6 | 12,5 |
| ct4, JSON-Modus streamend | 3,56 | **0,5** | 12,5 |
| ct4, direkt in die Datei | 3,58 | **0,0** | 12,5 |
| von Hand, `json.dumps` | 2,07 | 54,7 | 12,5 |

Das ist die Zahl, auf die es ankommt: **91 MB auf 0,5 MB**, Faktor 180, für
25 Prozent mehr Zeit. Auf einer Station mit 512 MB entscheidet das darüber, ob
die Seite entsteht. Die Zeit halbiert sich nebenbei, und die Ausgabe ist
kleiner, weil die handgesetzte Fassung Leerraum mitschreibt.

Der Ausgabefilter, im Textmodus der zweitgrösste Posten, ist hier ohnehin weg:
es wird keine Zeichenkette zusammengesetzt.

### Die Schwelle

**Steht.** `tests/bench/render.py` läuft im Standarddurchlauf zweimal, gegen die
installierte ct3 und gegen den Fork, und `compare.py --check` hält jeden Fall
gegen eine Untergrenze.

Verglichen wird ein **Verhältnis**, keine Millisekundenzahl. Beide Läufe
passieren auf derselben Maschine in derselben Minute, damit fällt deren
Geschwindigkeit heraus. Eine absolute Schwelle müsste für die langsamste
Maschine gesetzt werden, die den Lauf je ausführt, und finge dann nichts.

`tests/unit/test_bench_guard.py` füttert `compare.py` mit einer Regression und
verlangt, dass er fehlschlägt. Ein Wächter, der immer durchlässt, sagt nichts.
Ein Fall dort hält ausserdem die Namen der Untergrenzen gegen die Fälle, die der
Benchmark wirklich erzeugt: eine Untergrenze, deren Name abgewandert ist,
bewacht nichts, und nichts sonst würde das melden.

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

Stand 31-Aug-2026: erfüllt.

| | |
|---|---|
| `ct4 check` über die 136 Skin-Vorlagen | 1 Befund, und das ist weewx' eigener Testfall |
| Aufgaben zur Diagnostik | 10 von 10 |
| Kommandos | `check`, `context`, `reference`, `declare`, `mcp` |
| Ausgabeformen | Text, JSON, SARIF |

**Woher die Platzhalter kommen.** Nicht aus einem zweiten Parser, sondern aus
dem Code, den Cheetah ohnehin erzeugt: dort steht jeder Nachschlagevorgang als
Pfad mit Zeile und Spalte. Das ist die genaueste Quelle, die es gibt, weil es
das ist, was zur Laufzeit wirklich nachgeschlagen wird.

**Was die Anmeldung nicht darf: zu viel melden.** Ein Falschbefund ist
schlimmer als ein übersehener Tippfehler, weil er Leute dazu bringt, das
Werkzeug abzuschalten. Der erste Entwurf behandelte `$trend` wie einen Zeitraum
und meldete den unveränderten Seasons-Skin an. Jetzt sind nur die zehn Namen
geschlossen, die wirklich einen Zeitraum liefern; alles andere ist offen, und
dort wird nicht geprüft statt falsch. Der Docker-Lauf hält die erwartete Zahl
der Befunde fest.

**Was die Evals messen.** Nicht ein Sprachmodell, sondern die Diagnostik: ob
aus einer Meldung die Korrektur folgt, ohne die Vorlage zu kennen. Jeder Fall
prüft zusätzlich, dass die richtige Fassung *keinen* Befund erzeugt. Die zweite
Hälfte ist die wichtigere. Eine Aufgabe hat sofort etwas gefunden: die
Schema-Meldung nannte das fehlende Feld nur im Pfad, nicht im Meldungstext.
Wenn eine Aufgabe fällt, wird die Meldung besser, nicht die Aufgabe leichter.

### P4 — Compiler-Kern

- Lexer, CST, AST, Codegen über `ast`
- Source Maps, deterministische Ausgabe, persistenter Compile-Cache
- Alter Compiler bleibt im Repo als Referenz für den Diff-Prüfstand
- Direktiven-Plugins auf AST-Ebene, Ablösung von `macroDirectives`
- Darauf aufbauend: `ct4 fmt`, `ct4 ast`, Sprachserver, tree-sitter

**Fertig, wenn:** Korpus byte-identisch mit dem neuen Backend, Tracebacks zeigen
in die Vorlage.

Stand 31-Aug-2026: **teilweise.** Die drei Schichten des Kerns stehen und
tragen 1.317 der 1.636 Render-Fälle byte-identisch. Die Direktiven-Plugins
hängen weiter daran.

| | |
|---|---|
| Deterministische Ausgabe | steht |
| Tracebacks zeigen in die Vorlage | steht, im Text- und im JSON-Modus |
| Persistenter Compile-Cache | steht, 1,45x warm gemessen |
| Geltungsbereiche im Compiler | steht, Render 1,9x schneller |
| Lexer, CST, AST, Codegen über `ast` | **1.317 von 1.636**, siehe unten |
| Direktiven-Plugins auf AST-Ebene | hängt daran |

**Die drei Schichten.** `ct4/lang/lex.py` zerlegt eine Vorlage verlustfrei in
einen Tokenbaum: jedes Byte der Quelle steht in genau einem Token, und
Zusammenfügen ergibt wieder die Quelle. `ct4/lang/tree.py` baut daraus die
Blockstruktur und liest, welche Direktiven schliessen müssen, aus einem echten
ct3-Parser statt aus einer Liste im Code. `ct4/lang/codegen.py` erzeugt Python
über das `ast`-Modul, nicht über Zeichenkettenverkettung, und was dabei
herauskommt ist eine Unterklasse von ct3s `Template`.

**Die Regel: unvollständig und nie falsch.** Die Schicht sagt, was sie kann,
lehnt alles andere ab, und was sie annimmt, muss byte für byte dasselbe
rendern wie ct3. Gemessen wird das am Korpus, und eine Untergrenze im Test
hält es fest.

**Wie die Regel einmal gebrochen war.** Zwischendurch stand die Zahl bei 1.359,
und 24 dieser Fälle rendeten anders als ct3: der Generator las keine einzige
Compiler-Einstellung und nahm die Vorlagen trotzdem. Der eine Test, der das
gemerkt hätte, übersprang genau die Fälle mit Einstellungen. Beide Hälften
zusammen ergaben eine Lücke, die keine Zahl der Suite zeigte. Einstellungen
werden jetzt abgelehnt statt ignoriert, der Test überspringt nichts mehr, und
die 42 Fälle, die das kostet, sind der Preis für die Regel.

**Was noch fehlt, nach Kosten geordnet und gezählt.** Der Kopf von `#def` und
`#block`, 64 Fälle, wo hinter dem Namen ein Kommentar oder eine Parameterliste
steht, die die Schicht nicht liest. ct3s Ausdrucks-Platzhalter `$(...)` und
`$[...]`, 46 Fälle, für den der Lexer keine Regel hat. Die
Compiler-Einstellungen, 52. Danach `c'...'`-Zeichenketten, die Einzeiler-Form
mit Doppelpunkt und ein gutes Dutzend Direktiven mit je zwölf Fällen oder
weniger.

**Was der Korpus nicht zeigt, und was daraus wurde.** Der Korpus besteht aus
2.026 echten Vorlagen, und jede einzelne schreibt ihre Direktiven auf eigene
Zeilen. Über das, was passiert, wenn eine Direktive sich eine Zeile mit
Ausgabe teilt, sagt er nichts. Ein differenzieller Fuzzer baute 13.072
Vorlagen, die genau das tun, und fand **1.864 von 12.627 angenommenen mit
anderen Bytes als ct3** — 14,8 Prozent, in fünf Gruppen, keine davon im
Korpus.

Alle fünf waren derselbe Fehler: das übrig gebliebene Zeilenende wurde eine
Position zu früh ausgegeben. ct3 schreibt zuerst den Code der Direktive und
dann den Text dahinter, und wo dieser Text hinfällt, hängt an der Direktive:

| Direktive | wohin das Zeilenende gehört |
|---|---|
| öffnendes Tag (`#for`, `#if`, …) | **in** den Rumpf, als dessen erste Ausgabe |
| `#end` | **hinter** den Block, ct3 hat ihn schon geschlossen |
| `#echo` | hinter das, was `#echo` schreibt |
| `#stop` | nirgendwohin, es steht hinter dem `return` |

Dazu zwei Einzelheiten, die aus ct3s Quelltext kommen und nicht aus dem
Nachdenken: `eatMultiLineComment` klammert seinen ganzen Whitespace-Block mit
`not self.atEnd()`, deshalb lässt ein Blockkommentar am Dateiende seinen
Einzug stehen; und `endOfFirstLine` wird vor dem Fressen gemessen, `pos`
danach, deshalb ist ein einzeiliger Kommentar schon darüber hinaus, sobald
sein Zeilenende genommen ist.

**Stand: 0 von 12.765 falsch.** Angenommen werden mehr Vorlagen als vorher
(12.627 → 12.765), und keine davon rendert anders als ct3. Der Fuzzer liegt
als `tests/fuzz/whitespace.py` im Repo und läuft in `all` mit, 29 Sekunden.

**Wo es doch aus dem Puffer entschieden wird.** ct3 fragt nicht den Quelltext,
ob eine Zeile frei ist, sondern schneidet seinen ausstehenden Text auf den
letzten Zeilenumbruch zurück. Meist ist das dasselbe. Nicht dasselbe ist es
nach einem `#def`: dessen Rumpf wandert in eine Methode, das `L` aus
`L#def g` bleibt ausstehend, und ein `#slurp` zwei Zeilen weiter löscht es.
ct3 rendert dort gar nichts. Das nachzubauen bräuchte ct3s Chunk-Grenzen statt
der Stücke dieser Schicht — ein Textlauf mit einem Escape darin ist dort ein
Chunk und hier drei Stücke — also wird abgelehnt. Kosten: 186 Fuzz-Vorlagen,
null Korpusfälle.

**Geltungsbereiche.** Der Compiler führt einen Stapel der Namen, die er selbst
gebunden hat, und löst Platzhalter darauf ohne die SearchList auf. Zahlen und
Begründung stehen in Abschnitt 12. Der Punkt hier: das ist der erste Teil des
Compiler-Kerns, der wirklich gebraucht wurde, und er kam über die Performance
herein, nicht über den Umbau. Der Strict-Modus aus W2 braucht dieselbe
Buchführung.

**Determinismus.** `addTimestampsToCompilerOutput` steht jetzt auf `False`.
Zwei Übersetzungen derselben Vorlage liefern dieselben Bytes. Das ist die
Voraussetzung für alles Weitere: ohne sie lässt sich weder etwas vergleichen
noch zwischenspeichern.

**Tracebacks.** Die Zuordnung stand schon da und wurde weggeworfen: Cheetah
schreibt hinter jede erzeugte Anweisung, aus welcher Zeile und Spalte sie
stammt. `ct4.trace` liest das und hängt es an die Ausnahme. Aus einem Traceback
auf `DynamicallyCompiledCheetahTemplate.py:87` wird zusätzlich
`Vorlage: bericht.tmpl, Zeile 3, Spalte 1`. Angehängt, nicht ersetzt.

Im JSON-Modus zeigen die Herkunftsangaben auf die erzeugte Definition, nicht
auf die Vorlage des Autors. Die Brücke baut der Emitter: er merkt sich zu jeder
Zeile, die er schreibt, aus welcher Zeile der Vorlage sie stammt.

**Compile-Cache, mit ehrlicher Zahl.** Eingehängt an
`Template._CHEETAH_compilerClass`, also an der Stelle, die ct3 dafür vorsieht.
Gemessen an den 136 Skin-Vorlagen:

| | |
|---|---|
| nur `__init__` | 0,17 s |
| Parser | 0,87 s |
| Codegenerierung | 1,40 s |
| Pythons eigenes `compile()` | 0,70 s |
| end-to-end ohne Cache, bestes von drei | 1,44 s |
| end-to-end mit warmem Cache | 0,99 s |

Also **1,45x**, nicht mehr. Der Cache überspringt Parser und Codegenerierung;
was bleibt, ist Pythons eigenes `compile()` und `exec()`, und das sind zwei
Drittel der verbleibenden Zeit. Um da heranzukommen, bräuchte es eine Stelle
zum Einhängen, die ct3 nicht hat. Das ist einer der Gründe für den eigenen
Compiler.

**Was nicht angefangen ist und warum.** Ein verlustfreies CST plus Codegen über
das `ast`-Modul, das 1.772 Korpusfälle byte-identisch reproduziert, ist ein
Vorhaben für sich und nicht der nächste sinnvolle Schritt, solange der
Schwerpunkt JSON heisst. Der JSON-Modus übersetzt heute über eine
Cheetah-`#def`; ihn auf eigenen Codegen umzustellen hiesse, einen zweiten
Ausdrucksparser zu bauen, und genau das verbietet der Entwurf aus gutem Grund.
Der Umbau lohnt erst, wenn er beiden Modi dient.

### P5 — `strict`-Modus und Performance

- Kein Autocalling, kontextabhängiges Escaping im `markup`-Modus,
  `async`-Rendering, Sandbox für Vorschau und Agent-Schleifen
- Die Frame-Auflösung **nicht** ersatzlos entfernen: gemessen ist sie der
  schnelle Weg (Abschnitt 12). Sie fällt weg, wenn der Compiler aus P4
  Geltungsbereiche kennt und direkten Zugriff erzeugt, nicht vorher
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
- **Was der Korpus wirklich prüft, ist selbst eine Messung.** 1.772 Fälle sind
  eine Zahl, solange niemand fragt, was sie treffen. `python -m ct4.corpus
  coverage` schaltet je einen Mechanismus ab und zählt die Fälle, die es
  merken. Am 31-Aug-2026:

  | Mechanismus aus | Renderfälle | Compile-Fälle |
  |---|---|---|
  | `namemapper` | 882 von 1.636 (54 %) | 136 von 136 |
  | `locals` | **327 von 1.636 (20 %)** | 0 von 136 |
  | `autocalling` | 198 von 1.636 (12 %) | 132 von 136 |
  | `filters` | 57 von 1.636 (3 %) | 136 von 136 |
  | `stackframes` | **0 von 1.636** | 133 von 136 |
  | `knownlocals` (Kontrolle) | 0 | 0 |

  Nur die Renderspalte sagt etwas über Verhalten. Ein Compile-Fall vergleicht
  erzeugten Code, und ein Mechanismus, der dessen Schreibweise ändert, ändert
  alle, ohne dass sich eine Vorlage anders verhält. Getrennt zu zählen ist
  nicht Kosmetik: ungetrennt sähe `stackframes` nach 133 betroffenen Fällen
  aus, und das wäre falsch.

  **`useStackFrames` ändert null Verhalten, und das ist richtig so.** Der
  Ersatzpfad erzeugt `VFSL([locals()]+SL+[globals(), builtin], …)`, und das
  durchläuft dieselben vier Namensräume in derselben Reihenfolge wie das C-
  `VFFSL` (`PyEval_GetLocals`, SearchList, `PyEval_GetGlobals`,
  `PyEval_GetBuiltins`). Die beiden sind bauartbedingt gleich. Eine frühere
  Fassung dieses Absatzes las die Null als Loch im Korpus und forderte neue
  Fälle. Das war ein Fehlschluss.

  Was P5 wirklich angreift, misst die Zeile `locals`: dort wird der
  Locals-Namensraum selbst entfernt. **327 Renderfälle hängen daran**, verteilt
  über 79 verschiedene Tests und genau die Konstrukte, die Namen binden: `#for`,
  `#set`, `#def`, `#call`, `#block`, `#capture`, `#while`, `#break`,
  `#continue`. Der Korpus deckt das also gut ab.

  Zwei Fallen stecken in der Messung selbst, beide hier hineingelaufen. Ein
  Mechanismus muss abgeschaltet sein, **bevor** die erste Vorlage übersetzt
  wird: ein erzeugtes Modul bindet seine Nachschlagefunktionen beim Ausführen,
  und `Template.compile` legt die Klasse ab. Und `locals` muss
  `resolveKnownLocals` mit abschalten, weil die Abkürzung selbst eine Auflösung
  aus den Locals ist und nicht über `VFFSL` läuft; ohne das meldete die Messung
  301 statt 327.
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
