"""srv6_fabric.cli — user-facing command-line entry points.

Thin wrappers around srv6_fabric library modules. Each `main()` is
wired to a console-script entry in pyproject.toml so it ends up at
`/usr/local/bin/<name>` in the host-image Docker container.
"""
