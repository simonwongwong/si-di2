"""Talk to Shimano Di2 (11-speed, E-Tube) systems from Linux through an SM-BCR2."""
from .session import Di2Error, Session

__all__ = ['Di2Error', 'Session']
