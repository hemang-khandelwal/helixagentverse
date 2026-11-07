"""C owns this module. C1 starts with fixtures; C4 adds per-candidate sign-off."""

from contracts import RunResult


def render_run(result: RunResult) -> None:
    raise NotImplementedError("C1: render candidates, provenance, rejected list, run status")


def main() -> None:
    raise NotImplementedError("C1: build the fixture-driven interface")


if __name__ == "__main__":
    main()
