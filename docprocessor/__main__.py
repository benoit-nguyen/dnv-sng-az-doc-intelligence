"""
Main entry point for the docprocessor package.

Allows running the package as a module:
    python -m docprocessor <command> [args]
"""

from .cli import main

if __name__ == "__main__":
    main()
