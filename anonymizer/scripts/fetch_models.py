"""Download the two GLiNER models at their pinned commits and save each as a self-contained directory.

    uv run --extra model python scripts/fetch_models.py /model

gives /model/knowledgator and /model/urchade (the default of GLINER_MODEL_DIRS), each loadable with no network
(`local_files_only=True`): urchade's repository has no tokenizer, so it is taken from its encoder's repository, whose
commit is checked first. Meant for the image build (Task 7) and for local runs; it is the only step that downloads.
"""

from __future__ import annotations

import sys
from pathlib import Path

from anonymizer.detector import ENCODER_PINS, MODELS


def main(dest: str) -> None:
    from gliner import GLiNER  # noqa: PLC0415
    from huggingface_hub import HfApi, snapshot_download  # noqa: PLC0415

    for repo, revision in ENCODER_PINS.items():
        current = HfApi().model_info(repo).sha
        if current != revision:
            sys.exit(f"{repo} is at {current}, not the pinned {revision}: check it, then update ENCODER_PINS")
        snapshot_download(repo, revision=revision, allow_patterns=["*.json", "*.model"])
    for name, (repo, revision) in MODELS.items():
        target = Path(dest) / name
        model = GLiNER.from_pretrained(repo, revision=revision)
        model.save_pretrained(target)
        GLiNER.from_pretrained(str(target), local_files_only=True)  # it loads offline, or the build fails here
        print(f"{repo}@{revision} -> {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: fetch_models.py <destination directory>")
    main(sys.argv[1])
