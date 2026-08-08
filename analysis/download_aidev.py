#!/usr/bin/env python3
"""Download and verify the exact AIDev tables used by the feasibility pilot."""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path


REVISION = "68ed5f4b80d27a9e057fc57567f38bd322ac73ec"
BASE_URL = f"https://huggingface.co/datasets/hao-li/AIDev/resolve/{REVISION}"
FILES = {
    "pull_request.parquet": (16855612, "08f520b5ef36b6281f82090db09ac14aefc3534ee113886e7bc85131fd8d214c"),
    "pr_reviews.parquet": (7517041, "3b7996c6365000e9e7f91bb428ecff78b36290997bee4732e840b0f5cdce70f5"),
    "pr_timeline.parquet": (34108415, "9f45c52c0a7ffa4395ea6c7ce5beb993b3dfb2425ef3848c6d28e2e96471a6f8"),
    "pr_commits.parquet": (8314097, "339228e250d420cd20ffe4b90583d57040263427ea605c1c039631b8ca2bfb4f"),
    "pr_task_type.parquet": (2993264, "f32a97a45ac944f4ea473327e62d8f41361502c2b6b3778e76fb64c2b8896476"),
    "human_pull_request.parquet": (4387171, "910238f8fe5deb544ae82be343232ff6838f271f3de4b4e4787a515336a91248"),
    "human_pr_task_type.parquet": (715969, "5527d52bfd9605a25d1ed1ef03bce0e1cc217f6ffed936e2be1b80a04123e658"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/aidev"))
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)

    for name, (expected_size, expected_sha256) in FILES.items():
        destination = args.data_dir / name
        if not destination.exists():
            print(f"downloading {name}", flush=True)
            urllib.request.urlretrieve(f"{BASE_URL}/{name}?download=true", destination)
        actual_size = destination.stat().st_size
        actual_sha256 = sha256(destination)
        if actual_size != expected_size or actual_sha256 != expected_sha256:
            raise SystemExit(
                f"verification failed for {name}: size={actual_size}, sha256={actual_sha256}"
            )
        print(f"verified {name} {actual_sha256}")


if __name__ == "__main__":
    main()
