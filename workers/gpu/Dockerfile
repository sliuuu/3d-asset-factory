# GPU worker image for the DGX Spark (GB10, ARM64). NGC PyTorch images are multi-arch, which is
# why they are the base here; plain pip wheels for some 3D extensions are x86-only.
# UNVERIFIED on the DGX: the native extensions (TRELLIS.2 o_voxel / flex_gemm / nvdiffrast,
# Hunyuan3D custom_rasterizer and differentiable_renderer) compile from source here. If a build
# fails on ARM64, run this image on an x86 GPU host instead and point HUB_*_URL at it.
ARG BASE=nvcr.io/nvidia/pytorch:25.08-py3
FROM ${BASE}
ARG BACKEND=trellis2
ENV WORKER_BACKEND=${BACKEND} PIP_NO_CACHE_DIR=1 HF_HOME=/models/hf \
    OPENCV_IO_ENABLE_OPENEXR=1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
WORKDIR /opt
RUN pip install fastapi 'uvicorn[standard]' python-multipart pillow huggingface_hub transformers timm kornia
COPY install_backend.sh /opt/install_backend.sh
RUN bash /opt/install_backend.sh "${BACKEND}"
COPY server.py /opt/worker/server.py
WORKDIR /opt/worker
EXPOSE 9000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000"]
