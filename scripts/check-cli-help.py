#!/usr/bin/env python3
"""Run the CLI help smoke check from an unsigned source checkout."""

import sys

from debark import engine

engine.verify_official_copy = lambda: None
sys.argv = ["debark", "--help"]
raise SystemExit(engine.main())
