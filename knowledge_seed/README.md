Place optional exported knowledge bundles here for cloud/runtime hydration.

Expected layout:

- `collections.json`
- `source_registry.json`
- `chunks/*.jsonl`
- `cleaned/*` optional
- `intel/<collection>/*` optional

The runtime will never treat this folder as a second authority. It is only a
portable import source for the canonical `data/` runtime on systems where the
live knowledge volume starts empty.
