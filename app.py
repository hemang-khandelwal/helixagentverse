"""C owns this module. C1 starts with fixtures; C4 adds per-candidate sign-off."""

# C builds the screen here. Users enter a drug and inspect findings, sources,
# warnings, and rejected suggestions.



from contracts import RunResult


def render_run(result: RunResult) -> None:
    # Display one investigation: findings, supporting sources, checking results,
    # rejected findings, and whether the run finished. Still to build: the error
    # below deliberately stops this unfinished function.
    raise NotImplementedError("C1: render candidates, provenance, rejected list, run status")


def main() -> None:
    # Start the application. First build it using labelled example data; connect the
    # real investigation after the screen works. Still to build: the error below
    # deliberately stops this unfinished function.
    raise NotImplementedError("C1: build the fixture-driven interface")


if __name__ == "__main__":
    main()
