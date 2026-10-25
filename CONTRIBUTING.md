# Contributing

This page shows how to ask a question, report a bug or propose a change to s1d1-alliance. Everyone taking part follows
the [code of conduct](CODE_OF_CONDUCT.md).

## What this repository is

Everything that runs the free community instance at <https://s1d1.qoo.gy>: the landing page, the `site` and `ingest`
Workers, the anonymiser, the Terraform for Cloudflare, Google Cloud and Hetzner Cloud, and the Compose file of a
community node. [docs/operations.md](docs/operations.md) says how it is brought up and run.

Prompts sent to the service are governed by the [conditions of use](https://s1d1.qoo.gy/conditions/v1/). Contributing
code here does not change that.

## Questions and bugs

Open an [issue](https://github.com/scombat/s1d1-alliance/issues/new/choose) with the *Bug report* or *Question* form.
A vulnerability never goes in an issue: report it privately as [SECURITY.md](SECURITY.md) says.

## Propose a change

1. Fork the repository and branch from `main`.
2. Make the change, with its tests, and run the checks the CI runs:

   ```bash
   node --test "workers/**/*.test.js"
   (cd anonymizer && uv run --python 3.12 pytest -q -m "not slow")
   sh node/test/test_update.sh
   sh node/test/test_cloud_init.sh
   TF_CLOUD_ORGANIZATION=scombat terraform -chdir=terraform init -backend=false \
     && terraform -chdir=terraform validate
   uvx pre-commit run --all-files   # needs gitleaks installed (8.30.1 in CI)
   ```

3. Update the docs the change touches.
4. Open a pull request against `main`; its template lists what a review checks.

## Rules that matter here

- **Every change goes through a pull request.** `main` is protected, and every merge triggers a HCP Terraform plan and
  apply on production. Read the speculative plan on your PR.
- **Never commit a secret.** Tokens, keys and `*.tfvars` go to HCP Terraform or to the nodes. `gitleaks` guards the
  commit and the CI.
- **Images are pinned by digest**, and nodes check their cosign signature before starting them.
- **No dependency without a reason.** Say in the PR why it is needed.
- **Privacy invariants.** Never log prompts, headers or IP addresses. The anonymiser fails closed: when in doubt, a
  prompt does not reach the clean corpus.
- **The legal pages are not ours to change.** `site/conditions/`, `site/confidentialite/` and `site/mentions-legales/`
  are not under the Apache-2.0 licence (see [NOTICE](NOTICE)) and change only by the maintainer.

## Licence of contributions

s1d1-alliance is under the [Apache License 2.0](LICENSE). By opening a pull request, you agree that your contribution is
licensed under the same licence.
