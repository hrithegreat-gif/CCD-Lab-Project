"""Render Kubernetes manifests for deployment; standard library only.

Substitutes the image reference (and optionally an imagePullSecret) into the
k8s/*.yaml templates and prints or writes one multi-document YAML stream that
can be piped to `kubectl apply -f -`. No YAML parsing is needed on the target
host, so this runs on the EC2 instance with plain python3.

Examples:
    python scripts/render_k8s.py --image 123.dkr.ecr.ap-south-1.amazonaws.com/ccd-lab-serving:abc123
    python scripts/render_k8s.py --image ai-model-serving:dev --pull-secret none
    python scripts/render_k8s.py --image <uri> --include load-generator.yaml
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "k8s"

# Default core set for deployments; the load generator stays opt-in.
DEFAULT_MANIFESTS = ("namespace.yaml", "deployment.yaml", "service.yaml", "hpa.yaml")

IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9._\-/:@]+$")
PULL_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9._\-]+$")


def render_file(path: Path, image: str, pull_secret: str | None) -> str:
    """Return the file content with placeholders resolved, or '' if removed."""
    text = path.read_text(encoding="utf-8")
    if pull_secret is None:
        lines = []
        skipping = False
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped == "# BEGIN imagePullSecrets":
                skipping = True
                continue
            if stripped == "# END imagePullSecrets":
                skipping = False
                continue
            if not skipping:
                lines.append(line)
        text = "".join(lines)
    else:
        text = text.replace("__PULL_SECRET__", pull_secret)
    text = text.replace("__IMAGE__", image)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True,
                        help="full image reference to bake into the manifests")
    parser.add_argument("--pull-secret", default="ecr-pull",
                        help="imagePullSecrets name ('none' omits the block)")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR),
                        help="directory containing the k8s templates")
    parser.add_argument("--include", action="append", default=[],
                        metavar="FILE",
                        help="extra template to append, e.g. load-generator.yaml")
    parser.add_argument("--output", default="-",
                        help="output path ('-' for stdout)")
    args = parser.parse_args(argv)

    image = args.image.strip()
    if not image or not IMAGE_PATTERN.match(image):
        parser.error(f"invalid --image reference: {image!r}")
    if image == "__IMAGE__" or "__" in image:
        parser.error("--image still contains an unreplaced placeholder")

    pull_secret = None if args.pull_secret == "none" else args.pull_secret
    if pull_secret is not None and not PULL_SECRET_PATTERN.match(pull_secret):
        parser.error(f"invalid --pull-secret name: {pull_secret!r}")

    source_dir = Path(args.source_dir).resolve()
    ordered = list(DEFAULT_MANIFESTS)
    for extra in args.include:
        if extra not in ordered:
            ordered.append(extra)
    paths = []
    for name in ordered:
        path = source_dir / name
        if not path.is_file():
            print(f"render_k8s: missing template: {path}", file=sys.stderr)
            return 1
        paths.append(path)

    documents = [render_file(path, image, pull_secret) for path in paths]
    stream = "\n---\n".join(documents)
    if not stream.endswith("\n"):
        stream += "\n"

    leftovers = [token for token in ("__IMAGE__", "__PULL_SECRET__") if token in stream]
    if leftovers:
        print(f"render_k8s: unresolved placeholders remain: {leftovers}",
              file=sys.stderr)
        return 1

    if args.output == "-":
        sys.stdout.write(stream)
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(stream, encoding="utf-8", newline="\n")
        print(f"render_k8s: wrote {len(paths)} manifests to {output}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
