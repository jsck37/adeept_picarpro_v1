#!/usr/bin/env python3
"""Battery Monitor — placeholder (requires ADS7830 ADC, not present on v1)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main():
    print("[Battery] ADS7830 ADC not present on this hardware.")
    print("[Battery] Module disabled.")


if __name__ == '__main__':
    main()
