"""Compatibility entry point; implementation lives in platforms/linux."""
if not __package__:
    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ginput.platforms.linux.panel_ui import *  # noqa: F401,F403
else:
    from .platforms.linux.panel_ui import *  # noqa: F401,F403

if __name__ == '__main__':
    main()
