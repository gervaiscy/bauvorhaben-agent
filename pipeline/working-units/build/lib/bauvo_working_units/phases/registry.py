"""The fixed, explicit analysis sequence."""
from .aufgaben import service as aufgaben
from .offers import service as offers
from .timing import service as timing
from .consequences import service as consequences
from .applicability import service as applicability

PHASES = (aufgaben, offers, timing, consequences, applicability)
BY_PASS = {phase.PASS: phase for phase in PHASES}
