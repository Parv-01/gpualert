# Releasing

The release recipe for GPUAlert. Follow it top to bottom for every release.
Steps marked **(once)** only need to run on a new dev machine.

## 0. Prerequisites (once)

- A PyPI account at <https://pypi.org/account/register/> with 2FA enabled.
- A TestPyPI account at <https://test.pypi.org/account/register/> (separate from PyPI).
- API tokens for both, stored in `~/.pypirc`:

  ```ini
  [distutils]
    index-servers =
      pypi
      testpypi

  [pypi]
    username = __token__
    password = pypi-AgEIcHlwaS5vcmcCJ...

  [testpypi]
    repository = https://test.pypi.org/legacy/
    username = __token__
    password = pypi-AgENdGVzdC5weXBpLm9yZwIm...
  ```

  Set `chmod 600 ~/.pypirc`.

- `pip install --upgrade build twine`
- The GitHub CLI (`gh auth login`) if you want `gh release create` to publish the release notes.

## 1. Pre-flight gate

These must be green. If anything fails, stop and fix it.

```bash
pytest                                # 98 tests, 91% coverage
python local_test.py                  # 131 end-to-end checks
gpualert test-email                   # uses your real SMTP config — must deliver
```

## 2. Update the version number

Three places must agree:

```bash
# 1. pyproject.toml
version = "0.1.1"

# 2. gpualert/__init__.py
__version__ = "0.1.1"

# 3. CHANGELOG.md — promote [Unreleased] entries to the new version with today's date
## [0.1.1] — YYYY-MM-DD
```

Commit:

```bash
git add pyproject.toml gpualert/__init__.py CHANGELOG.md
git commit -m "chore(release): bump version to 0.1.1"
```

## 3. Build the artifacts

```bash
rm -rf dist build *.egg-info
python -m build --sdist --wheel
```

Two files land under `dist/`:

```
dist/gpualert-0.1.1-py3-none-any.whl
dist/gpualert-0.1.1.tar.gz
```

Verify metadata and README rendering:

```bash
twine check dist/*
```

Both must say `PASSED`. If `twine check` fails on the README, the long-description
won't render on PyPI's project page — fix the markdown and rebuild.

Record the SHA-256 of each artifact for the release notes:

```bash
sha256sum dist/*
```

## 4. Dry-run on TestPyPI

```bash
twine upload --repository testpypi dist/*
```

Then install from TestPyPI in a fresh venv and verify:

```bash
python -m venv /tmp/check-release
source /tmp/check-release/bin/activate
pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  gpualert
gpualert version             # must print the version you're publishing
gpualert --help              # all 6 commands listed
deactivate
rm -rf /tmp/check-release
```

`--extra-index-url` is required because TestPyPI does not host our runtime deps
(pydantic, typer, rich); pip needs the real PyPI as a fallback.

If anything is wrong, fix it, bump the version (TestPyPI does not allow overwriting),
and repeat.

## 5. Publish to real PyPI

```bash
twine upload dist/*
```

The project will be live at `https://pypi.org/project/gpualert/<version>/` within a
minute. Confirm by installing fresh:

```bash
python -m venv /tmp/check-real
source /tmp/check-real/bin/activate
pip install gpualert
gpualert version
deactivate
rm -rf /tmp/check-real
```

## 6. Tag and push the release

```bash
git tag -a v0.1.1 -m "v0.1.1"
git push origin v0.1.1
```

The `-a` makes it an annotated tag (carries author + date), which is what GitHub
treats as a release. Lightweight tags work but render with less metadata.

## 7. Cut the GitHub release

With the GitHub CLI:

```bash
gh release create v0.1.1 \
  --title "v0.1.1" \
  --notes-file <(sed -n '/## \[0.1.1\]/,/## \[/p' CHANGELOG.md | sed '$d') \
  dist/gpualert-0.1.1-py3-none-any.whl \
  dist/gpualert-0.1.1.tar.gz
```

Or do it manually at `https://github.com/Parv-01/gpualert/releases/new`, paste the
changelog section for this version into the body, and upload both artifacts as
release assets.

## 8. Post-release housekeeping

Open a new commit that prepares the next development cycle:

```bash
# In CHANGELOG.md, add a fresh [Unreleased] section above the tagged one:
## [Unreleased]

## [0.1.1] — 2026-05-25
...
```

Commit:

```bash
git add CHANGELOG.md
git commit -m "chore: reopen [Unreleased] section after 0.1.1"
git push origin main
```

## Rolling back a bad release

PyPI does **not** allow re-uploading a version. The recovery path is:

1. `pip install <package>==<previous-good-version>` works for users on their side.
2. Yank the broken release: `twine` doesn't yank; do it at
   `https://pypi.org/manage/project/gpualert/release/<bad-version>/` (the
   "Options" → "Yank this release" button). A yanked release stays installable for
   pins, but `pip install gpualert` (no pin) skips it.
3. Bump the patch version, fix forward, repeat the recipe.

## Version policy

GPUAlert follows [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html):

- **MAJOR** — breaking API / CLI changes. e.g. renaming `gpualert run`, removing
  `--no-notify`, changing the config schema in a way that needs a migration.
- **MINOR** — backwards-compatible features. New CLI flags, new notifier backends,
  new artifact patterns by default.
- **PATCH** — backwards-compatible fixes. Bug fixes, error message improvements,
  docs-only changes that ship with code (docs-only without code changes do not
  need a release).

Pre-1.0 caveat: while the version is `0.x.y`, the API contract is "best effort".
Breaking changes can land in `0.x+1.0` without a major bump. The 0 → 1 jump happens
when the CLI surface and config schema are considered stable.
