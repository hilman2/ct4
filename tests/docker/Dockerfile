# Environment for the corpus test bench and the tool's own tests.
#
# The image carries two Cheetah implementations side by side: the
# pip-installed ct3 as the reference, and the fork from this repo. Which
# of the two a run uses is decided by ct4.impl from sys.path. That is
# why the fork is deliberately not installed: an editable install entry
# would shadow the reference, and the comparison would measure the same
# thing twice.
FROM python:3.13-slim

# The C NameMapper has to be built. Without it ct3 silently turns off
# useStackFrames, and then the test bench measures a different semantics
# than the one that ships.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# The reference version is the one weewx users install today.
ARG CT3_VERSION=3.4.0.post5
RUN pip install --no-cache-dir "ct3==${CT3_VERSION}" setuptools

# Pinned versions: what runs here must not depend on when the image was
# built. Markdown and pygments are needed by the filter test suite of
# ct3.
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
