"""Freeze contexts so templates run without their application.

A weewx skin reads from objects that a running application and its
database provide. That is too expensive for testing, and out of reach
for an agent.

A fixture solves this by recording once what a template actually reads
from the context, and storing that as JSON. After that the same
template renders straight from the file, in milliseconds, without the
application.
"""

from ct4.fixture.record import Recorder, replay

__all__ = ["Recorder", "replay"]
