"""Build-time warmup: instantiate PaddleOCR once so its detection/recognition/
angle-classification models are downloaded into the image layer.

Run during `docker build` (see Dockerfile). With the models baked in, the
running container never does a cold network pull on the first /ocr request —
the only first-request cost is loading the on-disk models into memory.
"""

from paddleocr import PaddleOCR

# Same configuration as server._get_engine(); this triggers the model download.
PaddleOCR(use_angle_cls=True, lang="en", use_gpu=False, show_log=False)
print("PaddleOCR models pre-downloaded.")
