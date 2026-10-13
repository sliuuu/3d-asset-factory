#!/usr/bin/env bash
# Installs one model family's code. Weights download on first request into /models/hf.
set -euxo pipefail
case "$1" in
  trellis2)
    git clone --recursive --depth 1 https://github.com/microsoft/TRELLIS.2 /opt/TRELLIS.2
    cd /opt/TRELLIS.2
    # Upstream setup script; see its README for flags. Expect to iterate here on ARM64.
    bash setup.sh --basic --flash-attn --nvdiffrast --cumesh --o-voxel --flexgemm || true
    pip install -e . || true
    echo "/opt/TRELLIS.2" > "$(python -c 'import site;print(site.getsitepackages()[0])')/trellis2.pth"
    ;;
  hunyuan)
    git clone --depth 1 https://github.com/Tencent/Hunyuan3D-2 /opt/Hunyuan3D-2
    cd /opt/Hunyuan3D-2
    pip install -r requirements.txt
    pip install -e .
    (cd hy3dgen/texgen/custom_rasterizer && python setup.py install)
    (cd hy3dgen/texgen/differentiable_renderer && python setup.py install)
    ;;
  trellis-text)
    git clone --recursive --depth 1 https://github.com/microsoft/TRELLIS /opt/TRELLIS
    cd /opt/TRELLIS
    bash setup.sh --basic --xformers --flash-attn --diffoctreerast --spconv --mipgaussian --kaolin --nvdiffrast || true
    echo "/opt/TRELLIS" > "$(python -c 'import site;print(site.getsitepackages()[0])')/trellis.pth"
    ;;
  *) echo "unknown backend $1"; exit 1 ;;
esac
