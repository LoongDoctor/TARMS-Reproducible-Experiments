# GitHub Upload Guide

Recommended repository name:

```text
TARMS-Reproducible-Experiments
```

## Option A: Git command line

Create an empty GitHub repository. Do not ask GitHub to add a README, license or `.gitignore`, because all three already exist in this package.

From the extracted repository directory:

```bash
git init
git add .
git commit -m "release: TARMS reproducible experiments v1.0.0"
git branch -M main
git remote add origin https://github.com/<your-account>/TARMS-Reproducible-Experiments.git
git push -u origin main
git tag -a v1.0.0 -m "TARMS reproducible experiments v1.0.0"
git push origin v1.0.0
```

This repository intentionally has no GitHub Actions workflow, so an OAuth token does not need the additional `workflow` permission merely to push these files.

## Option B: GitHub web interface

1. Create an empty repository named `TARMS-Reproducible-Experiments`.
2. Choose **uploading an existing file**.
3. Upload the contents of the extracted top-level folder, preserving all subdirectories.
4. Confirm that `.gitignore`, `LICENSE`, `CITATION.cff`, `results/`, `src/`, `scripts/`, `tests/` and `fabric/` are present.
5. Commit directly to `main`.
6. Create a release tagged `v1.0.0`.

The command-line route is preferred because it preserves the complete directory tree more reliably.

## Post-upload checks

Clone the repository into a new directory and run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
make install
make verify
```

Record the immutable commit:

```bash
git rev-parse HEAD
```

After creating the GitHub release, retain:

- repository URL;
- tag or release name (`v1.0.0`);
- full commit SHA;
- license (`Apache-2.0`);
- archive DOI, if a service such as Zenodo is connected later.

These values can then be inserted into the study's Data Availability and Code Availability statements.

