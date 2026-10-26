"""The old path of the desktop backend, now litloop.desktop.server; kept for installed desktop apps that start python -m litloop.app_server; remove after one release."""

from .desktop.server import create_app, main  # noqa: F401

if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
