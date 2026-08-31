"""Die Eval-Suite als Teil des Testlaufs.

Jede Aufgabe wird ein eigener Test. Faellt eine, ist nicht die Aufgabe
falsch, sondern die Meldung zu duenn: dann wird die Meldung besser,
nicht die Aufgabe leichter.
"""

from __future__ import annotations

import pytest

from ct4 import evals

FAELLE = evals.load()


def test_es_gibt_aufgaben():
    assert FAELLE


@pytest.mark.parametrize("fall", FAELLE, ids=lambda f: f.id)
def test_aus_der_meldung_folgt_die_korrektur(fall):
    ergebnis = evals.run_case(fall, base_dir=evals.CASES)
    assert ergebnis.passed, "\n".join(ergebnis.reasons)
