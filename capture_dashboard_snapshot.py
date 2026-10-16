"""Compatibility entry point for the dashboard snapshot tool."""

from scripts.online import capture_dashboard_snapshot as _impl

requests = _impl.requests


def capture(*args, **kwargs):
    _impl.requests = requests
    return _impl.capture(*args, **kwargs)


def main():
    _impl.requests = requests
    return _impl.main()


if __name__ == "__main__":
    main()
