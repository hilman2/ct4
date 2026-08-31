"""Der Vergleichskorpus und sein Pruefstand.

Ein Korpusfall besteht aus einer Vorlage, einem Kontext und der Ausgabe,
die ct3 dafuer liefert. Der Pruefstand rendert jeden Fall mit einer
gewaehlten Implementierung und vergleicht Byte fuer Byte. Das ist das
Abnahmekriterium aus PLAN.md, Abschnitt 8: Vertraeglichkeit wird
gemessen, nicht behauptet.
"""

from ct4.corpus.case import Case, read_jsonl, write_jsonl

__all__ = ["Case", "read_jsonl", "write_jsonl"]
