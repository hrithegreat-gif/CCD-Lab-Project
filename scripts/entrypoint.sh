#!/bin/sh
# Image entrypoint: materialize the baked model cache into the writable
# per-Pod cache directory when they differ (Kubernetes mounts an emptyDir at
# MODEL_CACHE_DIR). Locally MODEL_CACHE_DIR already points at the baked store.
set -eu

SRC="/opt/model-cache"
DST="${MODEL_CACHE_DIR:-$SRC}"

if [ -f "$SRC/.model-baked" ] && [ "$DST" != "$SRC" ] && [ ! -f "$DST/.model-baked" ]; then
    echo "entrypoint: populating $DST from baked model cache"
    mkdir -p "$DST"
    cp -a "$SRC/." "$DST/"
fi

exec "$@"
