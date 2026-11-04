# Offline video production core — proposed implementation input

Fresh generic specifications and synthetic fixtures only. No existing product source or media is included.

Build a reusable offline Python video core: episode JSON → validated integer-frame timeline → explicit FFmpeg argument plan → optional local render → machine-readable readback receipt. Consumers supply their own source assets separately. This package requests implementation; it does not contain an implemented renderer.

Python 3.11+ standard library for the core. FFmpeg/ffprobe are optional external executables, discovered explicitly. The default CLI action is a side-effect-free plan. Real rendering requires an explicit output directory and render flag. No provider API, network downloads, credentials, publishing or background scheduler is part of this component.

Read SPEC.md, schemas/episode.schema.json and ACCEPTANCE.md. The sample is six seconds of generated solid colors and synthetic captions; no private story, image, voice or company integration is provided.
