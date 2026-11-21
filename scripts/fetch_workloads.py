"""Download the datasets the real jobs work on (cached).

  * Imagenette-160 validation split (fast.ai, 3,925 ImageNet photos)  -> image tagging job
  * SQuAD v1.1 validation passages (2,067 Wikipedia paragraphs)       -> embedding job
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W = ROOT / "data" / "raw" / "workloads"


def main():
    W.mkdir(parents=True, exist_ok=True)
    if not (W / "imagenette2-160" / "val").exists():
        print("downloading Imagenette-160 (94 MB)...")
        raw = urllib.request.urlopen("https://s3.amazonaws.com/fast-ai-imageclas/imagenette2-160.tgz", timeout=300).read()
        with tarfile.open(fileobj=io.BytesIO(raw)) as t:
            t.extractall(W)
    if not (W / "passages.json").exists():
        import pandas as pd
        print("downloading SQuAD v1.1 validation passages...")
        url = "https://huggingface.co/datasets/rajpurkar/squad/resolve/main/plain_text/validation-00000-of-00001.parquet"
        (W / "squad_val.parquet").write_bytes(urllib.request.urlopen(url, timeout=300).read())
        d = pd.read_parquet(W / "squad_val.parquet").drop_duplicates("context")
        (W / "passages.json").write_text(json.dumps([{"title": t, "text": x} for t, x in zip(d.title, d.context)]))
    n_img = sum(1 for _ in (W / "imagenette2-160" / "val").rglob("*.JPEG"))
    n_txt = len(json.loads((W / "passages.json").read_text()))
    print(f"workloads ready: {n_img} images, {n_txt} passages")


if __name__ == "__main__":
    sys.exit(main())
