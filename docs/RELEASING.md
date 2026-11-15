# Publishing a release

The Marketplace installs the dedicated `sigen_mysigen.zip` release asset. Its contents are the committed files under `custom_components/sigen_mysigen/`, without an enclosing directory. The process mirrors the one used for Sigenergy Local Modbus.

1. Set the manifest version, for example `0.1.0b1`. Keep `hacs.json` configured with `zip_release: true` and `filename: sigen_mysigen.zip`.
2. Add optional release notes in `docs/releases/<version>.md`. Otherwise GitHub generates the notes.
3. Merge the release preparation into `main` and wait for the unit tests, Hassfest and the HACS action to pass.
4. Create and push the matching tag, for example `v.0.1.0b1`, on that exact commit. Do not create a published release manually first.
5. The **Publish installation ZIP** workflow runs the tests, Hassfest and the HACS action, builds and checks the package, uploads it to a draft release, downloads and compares the uploaded bytes, then publishes.
6. Check the published asset, tag, manifest version and prerelease status. Test installation through the Marketplace.

Alpha, beta and release-candidate manifest suffixes (`a1`, `b1`, `rc1`) produce prereleases and leave the latest stable release unchanged. Stable versions are marked latest when published.

The workflow can be retried after a failure with a draft release. It may replace an asset in that unpublished draft, but refuses to replace a published release. Never move published tags or replace assets of a published release; prepare a new version instead.

For a local package check after committing:

```sh
python scripts/build_release.py --tag v.0.1.0b1 --output dist/sigen_mysigen.zip
```

The builder always reads `HEAD` through Git, so local caches and uncommitted edits are excluded. A tag/version mismatch stops the build.

Unlike the Modbus repository, this workflow does not open an Announcements discussion, because Discussions are not enabled here.
