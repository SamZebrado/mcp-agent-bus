# Release Checklist

## Pre-release items

- [ ] Confirm license before public release
- [ ] Confirm package name is unique on PyPI
- [ ] Confirm README examples work correctly
- [ ] Run `bash run_smoke.sh` and verify all tests pass
- [ ] Verify .gitignore is correct
- [ ] Review docs for overclaiming (do not claim unverified capabilities)
- [ ] Review docs for correct language (no absolute claims like "guaranteed", "all", etc.)
- [ ] Check that no secrets or keys are in the repository
- [ ] Verify no node_modules or dist directories are present
- [ ] Confirm all source code follows security best practices

## Git operations (do only after user approval)

- [ ] Create git tag after user approval
- [ ] Push to remote only after user approval

## Items NOT to do

- Do NOT push without user approval
- Do NOT create remote without user approval
- Do NOT create tag without user approval
- Do NOT add a license unless explicitly required
- Do NOT add node_modules or dist to repository
- Do NOT commit secrets or keys
