import sys

if __package__:                      # python -m hb ...
    from .cli import main
else:                                # python -I <repo>/hb/__main__.py ...  (how AI tools launch the MCP server:
    from pathlib import Path         # -I keeps the user's project folder and PYTHON* env vars out of sys.path)
    _root = str(Path(__file__).resolve().parent.parent)
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != Path(__file__).resolve().parent]
    sys.path.insert(0, _root)
    from hb.cli import main

sys.exit(main())
