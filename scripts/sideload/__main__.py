"""Entry point. `python3 -m sideload` runs the CLI; `tui` opens the interface."""

import sys


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "tui":
        from .tui import run_tui
        return run_tui(argv[1:])
    from .cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
