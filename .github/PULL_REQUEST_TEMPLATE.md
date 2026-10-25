<!-- What changes and why, in a few lines. -->

- [ ] the checks pass: `node --test "workers/**/*.test.js"`, the anonymiser tests, `sh node/test/test_update.sh`,
      `sh node/test/test_cloud_init.sh`, `terraform validate`, `uvx pre-commit run --all-files`
- [ ] no secret in the diff
- [ ] privacy invariants kept: no prompt, header or IP logged; the anonymiser still fails closed
- [ ] Terraform plan reviewed (the HCP speculative plan check on this PR)
- [ ] the docs are updated, if the change needs it
