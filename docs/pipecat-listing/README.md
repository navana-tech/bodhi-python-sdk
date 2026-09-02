# Getting Bodhi listed on docs.pipecat.ai

Pipecat no longer merges vendor services into its core repo. New services are
[community integrations](https://github.com/pipecat-ai/pipecat/blob/main/COMMUNITY_INTEGRATIONS.md):
the code stays in our repository, and Pipecat lists it on the Supported
Services page with a `Community` maintainer badge and its own service page.
Gnani, Quickdial, Ringg and SLNG are all listed this way.

`bodhi.mdx` in this directory is the drafted service page. To submit:

1. Fork https://github.com/pipecat-ai/docs
2. Copy `bodhi.mdx` to `api-reference/server/services/stt/bodhi.mdx`, filling in
   the two `TODO-github-username` fields in the `<CommunityMaintained>` block.
3. Add a row to `api-reference/server/services/supported-services.mdx` in the
   Speech-to-Text table, keeping the column alignment of the rows around it:

   ```
   | [Bodhi](/api-reference/server/services/stt/bodhi)               | `uv add "bodhi-sdk[pipecat]"`                                                 | Community  |
   ```

4. Register the page in `docs.json` under the STT navigation group, and add a
   redirect entry following the pattern of the neighbouring services.
5. Open the PR with a link to a 30-60 second demo video showing transcription
   working and an interruption being handled.
6. Join https://discord.gg/pipecat and post the PR in `#community-integrations`.

## What the guide requires of our repository

| Requirement | Status |
|---|---|
| Source code following Pipecat patterns | `bodhi/integrations/pipecat_stt.py`, on `WebsocketSTTService` as the guide prescribes |
| Foundational single-file example | `examples/pipecat_mic_bot.py`, plus `examples/pipecat_stream_wav.py` |
| README: intro, install, pipeline usage, how to run the example | the Pipecat section of the root README |
| README: Pipecat version compatibility | stated as tested on 1.4.0 and 1.8.1 |
| README: company attribution | done — "Maintained by Navana Tech, who build Bodhi" |
| Permissive LICENSE (BSD-2 or equivalent) | done — MIT `LICENSE`, matching the classifier `setup.py` already declared |
| Docstrings on the source | present, in Pipecat's convention |
| Changelog | `CHANGELOG.md` |
| Interim + final transcription frames | yes |
| Filter results below 50% confidence | yes, `min_confidence` defaults to 0.5 |
