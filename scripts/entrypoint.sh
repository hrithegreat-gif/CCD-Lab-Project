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
    # emptyDir's root belongs to root:fsGroup. The application can create
    # children but cannot preserve that directory's timestamps/ownership.
    # Copy entries without archive metadata, and publish the marker LAST so
    # a failed/interrupted copy is retried on the next container start.
    for entry in "$SRC"/* "$SRC"/.[!.]* "$SRC"/..?*; do
        [ -e "$entry" ] || [ -L "$entry" ] || continue
        [ "${entry##*/}" = '.model-baked' ] && continue
        cp -R "$entry" "$DST/"
    done
    touch "$DST/.model-baked"
fi

exec "$@"
