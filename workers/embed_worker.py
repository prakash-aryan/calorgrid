"""Real job 1: document embeddings for semantic search (RAG index).

Model: sentence-transformers/all-MiniLM-L6-v2 (CPU).  Data: SQuAD v1.1 validation
passages (2,067 Wikipedia paragraphs, CC BY-SA).  Output: 384-d vectors per passage.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base import Worker, main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "raw" / "workloads" / "passages.json"


class EmbedWorker(Worker):
    name = "embed"
    batch_size = 32

    def load_items(self):
        docs = json.loads(CORPUS.read_text())
        return docs * int(os.environ.get("HS_REPEAT", "1"))      # benchmark: repeat corpus for longer runs

    def setup(self):
        import torch
        torch.set_num_threads(int(os.environ.get("HS_THREADS", "4")))
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu")

    def process(self, batch):
        return self.model.encode([d["text"] for d in batch], batch_size=len(batch),
                                 normalize_embeddings=True, show_progress_bar=False).astype(np.float16)

    def describe(self, batch, out):
        return batch[-1]["title"].replace("_", " ")


if __name__ == "__main__":
    main(EmbedWorker)
