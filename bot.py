#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Backward-compatible entrypoint.

Prefer running:
- `python -m tbssa`
- or installed console script `tbssa`
"""

from tbssa.__main__ import main


if __name__ == "__main__":
    main()
