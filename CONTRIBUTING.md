# Contributing

Thanks for your interest in GEL. Bug reports, docs fixes and focused pull requests are welcome.

## Setup

Follow [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md). For development you also need pytest:

```
py -3 -m pip install lark watchdog pillow pytest
tools\setup_smconv.bat
py -3 -m pytest tests
```

The end-to-end build tests need Windows (the bundled Asar is a Windows DLL).

## Pull requests

- Keep changes small and focused; explain the why in the description.
- Add or update tests in `tests/` for compiler, optimiser or converter changes.
- Compiler changes must keep generated output correct: the peephole tests compare optimised and
  unoptimised behaviour, so run the full suite before submitting.
- Update `docs/` when you change the language or the build.
- By contributing you agree your work is released under the MIT licence in [LICENSE](LICENSE).

## Reporting bugs

Please include the GEL snippet (or project), the command you ran, the full error output and your
Python version. Open issues with the templates provided.
