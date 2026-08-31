"""The eval suite as part of the test run.

Every task becomes its own test. If one fails, the task is not wrong,
the message is too thin: then the message gets better, not the task
easier.
"""

from __future__ import annotations

import pytest

from ct4 import evals

TASKS = evals.load()


def test_there_are_tasks():
    assert TASKS


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_the_message_implies_the_fix(task):
    result = evals.run_case(task, base_dir=evals.CASES)
    assert result.passed, "\n".join(result.reasons)
