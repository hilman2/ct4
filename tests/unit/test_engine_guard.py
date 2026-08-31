"""The guard against running the tooling on a CT3 engine.

The failure it exists for looks like success. CT3 and Cheetah4 both
install a package called Cheetah; whichever went in last overwrote the
other, pip says nothing, and every ct4 command keeps answering. Only
the engine underneath is the one this project forked away from.

So the cases below care about two things: that the guard fires at all,
and that what it says is enough to act on.
"""

from __future__ import annotations

import pytest

from ct4 import cli, engine


def test_the_engine_here_is_the_right_one():
    # If this fails, the run is against CT3 and every other test in the
    # suite is measuring the wrong thing.
    assert engine.matches(), engine.complaint()
    assert engine.version()[0] >= engine.REQUIRED


def test_the_guard_fires_on_an_older_engine(monkeypatch):
    monkeypatch.setattr(engine, "version", lambda: (3, 4, 0, "final", 5))
    assert not engine.matches()
    with pytest.raises(SystemExit):
        engine.require()


def test_the_guard_lets_the_right_engine_through():
    engine.require()


def test_the_complaint_says_what_to_do(monkeypatch):
    monkeypatch.setattr(engine, "version", lambda: (3, 4, 0, "final", 5))
    text = engine.complaint()
    # The version that is actually loaded, so nobody has to go looking.
    assert "3.4.0" in text
    # The way out.
    assert "pip install --force-reinstall --no-deps Cheetah4" in text
    # And the trap on the way out: uninstalling CT3 takes the files
    # Cheetah4 now owns with it.
    assert "uninstall CT3" in text


def test_the_complaint_names_who_ships_a_cheetah_package(monkeypatch):
    monkeypatch.setattr(engine, "version", lambda: (3, 4, 0, "final", 5))
    monkeypatch.setattr(engine, "owners",
                        lambda: ["CT3 3.4.0 -> /x/Cheetah/__init__.py"])
    assert "CT3 3.4.0" in engine.complaint()


def test_owners_survives_an_environment_it_cannot_read(monkeypatch):
    # Only ever called when something is already wrong. It must not
    # turn a clear complaint into a traceback.
    assert isinstance(engine.owners(), list)


def test_the_command_line_refuses_before_it_renders(monkeypatch):
    monkeypatch.setattr(engine, "version", lambda: (3, 4, 0, "final", 5))
    with pytest.raises(SystemExit):
        cli.main(["declare"])


def test_help_still_answers_on_a_broken_install(monkeypatch, capsys):
    # The check sits after argument parsing on purpose: somebody with a
    # mixed installation should still be able to read the help.
    monkeypatch.setattr(engine, "version", lambda: (3, 4, 0, "final", 5))
    with pytest.raises(SystemExit) as error:
        cli.main(["--help"])
    assert error.value.code == 0
    assert "check" in capsys.readouterr().out
