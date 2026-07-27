from __future__ import annotations

import sys

from nightwatch.cli import build_parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    # Default command: serve
    if not getattr(args, "cmd", None):
        args = parser.parse_args(["serve", *sys.argv[1:]])
    func = getattr(args, "func", None)
    if not func:
        parser.print_help()
        raise SystemExit(2)
    raise SystemExit(func(args))


if __name__ == "__main__":
    main()

