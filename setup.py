#!/usr/bin/env python
"""Nur noch fuer die C-Erweiterung da.

Alles Uebrige steht in pyproject.toml. setuptools kann Erweiterungen
bisher nicht rein deklarativ beschreiben, deshalb bleibt diese Datei.
"""

from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension("Cheetah._namemapper", ["Cheetah/c/_namemapper.c"]),
    ],
)
