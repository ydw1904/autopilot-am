"""Shared pytest setup: make `code/` importable so tests can `import db` etc.

Mirrors the sys.path insert that test_aircraft_aliases.py does on its own.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
