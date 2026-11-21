"""Real job 2: image tagging (ImageNet classes) - same model family as MLPerf 'resnet'.

Model: torchvision ResNet-50 (IMAGENET1K_V2), CPU.  Data: Imagenette-160 validation split
(3,925 photos, fast.ai, subset of ImageNet).  Output: top-5 class ids + probabilities.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from base import Worker, main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "data" / "raw" / "workloads" / "imagenette2-160" / "val"


class ImageWorker(Worker):
    name = "image_tag"
    batch_size = 16

    def load_items(self):
        return sorted(str(p) for p in IMAGES.rglob("*.JPEG")) * int(os.environ.get("HS_REPEAT", "1"))

    def setup(self):
        import torch
        import torchvision
        torch.set_num_threads(int(os.environ.get("HS_THREADS", "4")))
        self.torch = torch
        w = torchvision.models.ResNet50_Weights.IMAGENET1K_V2
        self.model = torchvision.models.resnet50(weights=w).eval()
        self.tf = w.transforms()
        self.labels = w.meta["categories"]

    def process(self, batch):
        from PIL import Image
        x = self.torch.stack([self.tf(Image.open(p).convert("RGB")) for p in batch])
        with self.torch.inference_mode():
            prob = self.model(x).softmax(-1)
        top = prob.topk(5)
        self._last = self.labels[int(top.indices[-1, 0])]
        return np.concatenate([top.indices.numpy(), top.values.numpy()], axis=1).astype(np.float32)

    def describe(self, batch, out):
        return f"{Path(batch[-1]).name} -> {self._last}"


if __name__ == "__main__":
    main(ImageWorker)
