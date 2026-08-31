# Umgebung fuer den Korpus-Pruefstand und die Tests des Werkzeugs.
#
# Im Bild stehen zwei Cheetah-Implementierungen nebeneinander: das per pip
# installierte ct3 als Referenz und der Fork aus diesem Repo. Welche von
# beiden ein Lauf benutzt, entscheidet ct4.impl anhand von sys.path.
# Deshalb wird der Fork bewusst nicht installiert: ein editierbarer
# Installationseintrag verdeckte die Referenz, und der Vergleich maesse
# zweimal dasselbe.
FROM python:3.13-slim

# Der C-NameMapper muss gebaut werden. Ohne ihn schaltet ct3 still
# useStackFrames ab, und dann misst der Pruefstand eine andere Semantik
# als die, die ausgeliefert wird.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Die Referenzversion ist die, die weewx-Anwender heute installieren.
ARG CT3_VERSION=3.4.0.post5
RUN pip install --no-cache-dir "ct3==${CT3_VERSION}" setuptools

# Feste Versionen: was hier laeuft, soll nicht davon abhaengen, wann das
# Bild gebaut wurde. Markdown und pygments braucht die Filter-Testsuite
# von ct3.
RUN pip install --no-cache-dir \
        "pytest==8.4.2" "pytest-xdist==3.8.0" "pytest-timeout==2.4.0" \
        "ruff==0.14.4" "mypy==1.18.2" \
        "Markdown==3.10" "pygments==2.19.2"

RUN useradd --create-home --uid 1000 ct4
USER ct4
WORKDIR /work

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/bin/sh", "/repo/tests/docker/entrypoint.sh"]
CMD ["all"]
