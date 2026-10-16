# version.py
"""The one place the version is written. It is also the version stamped into config.toml.

A change to a setting (add, remove, rename, change a default or its wording) needs a minor bump;
see CLAUDE.md. Patch bumps never touch users' config files.
"""
__version__ = "0.1.0"
