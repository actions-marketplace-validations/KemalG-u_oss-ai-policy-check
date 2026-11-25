# Releasing

A release is a pushed tag `vX.Y.Z`. The workflow `.github/workflows/release.yml` then runs the tests, builds the
sdist and the wheel, checks the wheel contents and publishes to PyPI through
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OpenID Connect). No PyPI token is stored in
the repository or in a GitHub secret. The `id-token: write` permission exists only on the `publish` job, which
runs in the GitHub environment `pypi`.

## One-time setup (before the first release)

Checked on 2026-11-22: the PyPI name `oss-ai-policy-check` was free (HTTP 404 on
`https://pypi.org/pypi/oss-ai-policy-check/json`) and `KemalG-u/oss-ai-policy-check` did not exist on GitHub.

1. **Create the GitHub repository** `KemalG-u/oss-ai-policy-check` (public) and push `main` with
   `git push origin main`. Never use `git push --all` or `git push --mirror`: they would also publish
   local branches and tags that are not meant to be public. The owner and name
   must match what is entered at PyPI in step 3 exactly; `action.yml`, `docs/action.md` and the README already
   use `KemalG-u/oss-ai-policy-check`.
2. **Create the GitHub environment `pypi`**: repository Settings, Environments, New environment, name `pypi`.
   Then, under the environment's settings:

   - Add yourself as a **required reviewer** (mandatory, not optional): a pushed tag then waits for your
     approval before anything is published, so a tag pushed by mistake or by a compromised credential does not
     reach PyPI on its own.
   - Under "Deployment branches and tags" choose "Selected branches and tags" and add a **tag rule `v*`**
     (and no branch rule), so only a `vX.Y.Z` tag can deploy to `pypi`.
3. **Create a pending publisher at PyPI** (a pending publisher creates the project on its first upload, so the
   project does not need to exist yet). Sign in to pypi.org, Account settings, Publishing, "Add a new pending
   publisher", GitHub tab, with these values:

   | field | value |
   |---|---|
   | PyPI project name | `oss-ai-policy-check` |
   | Owner | `KemalG-u` |
   | Repository name | `oss-ai-policy-check` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   A pending publisher does not reserve the name: register it shortly before the first tag.
4. **Check the release content.** `__version__` in `src/oss_ai_policy_check/__init__.py` (the version in
   `pyproject.toml` is read from it), the dated `CHANGELOG.md` section, and a green `ci` run on `main`.

## Each release

```
git tag v0.3.0
git push origin v0.3.0
```

Push the branch and the tag by name, one at a time (`git push origin main`, `git push origin v0.3.0`), never
with `--all` or `--mirror`. The tag must equal `v` plus `__version__`; the first job fails otherwise, and
nothing is published. Approve the `publish` job in the Actions tab (the environment requires your review).
Afterwards:

- `uvx oss-ai-policy-check --version` in a clean shell prints the new version.
- The PyPI file pages show a provenance attestation (PEP 740); the workflow passes `attestations: true` to
  `pypa/gh-action-pypi-publish`, which signs the files through the same Trusted Publishing identity.
- Create a GitHub Release from the tag (Releases, "Draft a new release", choose the tag, paste the changelog
  section).
- The GitHub Action needs no extra step: `uses: KemalG-u/oss-ai-policy-check@v0.3.0` installs the code at that
  tag from its own checkout.

## GitHub Marketplace (optional)

`action.yml` at the repository root has `name`, `description` (125 characters or fewer) and `branding`. To list
it, open the GitHub Release draft and tick "Publish this Action to the GitHub Marketplace". GitHub asks you to
accept the Marketplace developer agreement once, and requires two-factor authentication and an action name that
no other Marketplace action or GitHub user/organization uses. If the name is taken, change `name:` in
`action.yml` before tagging.

## If something goes wrong

- The workflow fails before the publish job: nothing reached PyPI. (`build` and `twine` are pinned in
  `release.yml`; update the pins deliberately.) Fix, delete the tag
  (`git push origin :refs/tags/v0.3.0`, `git tag -d v0.3.0`) and tag again.
- The publish job fails with a trusted-publisher error: the five fields in step 3 do not match the repository,
  workflow file, or environment name exactly.
- PyPI never accepts the same version twice, even after a deletion. A bad release is yanked in the project's
  settings and fixed with the next version number.
