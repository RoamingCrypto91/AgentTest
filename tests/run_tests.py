#!/usr/bin/env python3
"""Entry point for the Reverie test suite.  See tests/framework.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
