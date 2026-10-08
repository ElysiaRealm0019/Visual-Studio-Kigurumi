"""Probe SiliconFlow image generation behaviour before wiring it into KigCraft.

Reads the API key from SILICONFLOW_API_KEY. Results (images + summary.json) go to
runtime/siliconflow-probe/<timestamp>/ by default.

Examples (run with the backend virtualenv, which has httpx and Pillow):

  # 1. How does Qwen-Image-Edit choose its output size? Sends blank canvases of several shapes.
  python tools/siliconflow_probe.py size-test

  # 2. Real front-view attempt: character reference + product style reference + extra reference.
  python tools/siliconflow_probe.py generate \
      --image path/to/character-front.png --image ref/product-reference.png \
      --prompt-file path/to/prompt.txt --pad-first-to 800x1100
"""

import argparse
import base64
import io
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen-Image-Edit-2509"
IMAGE_FIELDS = ("image", "image2", "image3")
SIZE_TEST_CANVASES = {
    "square-1024": (1024, 1024),
    "front-800x1100": (800, 1100),
    "turnaround-1584x1056": (1584, 1056),
    "large-3000x2000": (3000, 2000),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.getenv("SILICONFLOW_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--steps", type=int, default=None, help="num_inference_steps")
    parser.add_argument("--cfg", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--negative-prompt", default=None)
    parser.add_argument("--max-input-side", type=int, default=2048)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--out", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("size-test", help="send blank canvases of several shapes and record output sizes")

    generate = sub.add_parser("generate", help="send up to three reference images with a prompt")
    generate.add_argument("--image", action="append", type=Path, default=[], help="repeat up to 3 times")
    prompt_group = generate.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-file", type=Path)
    generate.add_argument("--pad-first-to", default=None, help="WxH; pad the first image onto a white canvas of this ratio")
    generate.add_argument("--repeat", type=int, default=1)

    args = parser.parse_args()
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        print("SILICONFLOW_API_KEY is not set", file=sys.stderr)
        return 2

    out_dir = args.out or REPO_ROOT / "runtime" / "siliconflow-probe" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "size-test":
        cases = [
            (name, "Change the background to a light blue gradient.", [Image.new("RGB", size, "white")])
            for name, size in SIZE_TEST_CANVASES.items()
        ]
    else:
        if not 1 <= len(args.image) <= 3:
            parser.error("generate needs 1 to 3 --image values")
        prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8")
        images = [Image.open(path).convert("RGB") for path in args.image]
        if args.pad_first_to:
            width, height = (int(value) for value in args.pad_first_to.lower().split("x"))
            images[0] = pad_to_ratio(images[0], width / height)
        cases = [(f"generate-{index}", prompt, images) for index in range(1, args.repeat + 1)]

    summary = []
    with httpx.Client(timeout=args.timeout) as client:
        for name, prompt, images in cases:
            summary.append(run_case(client, args, api_key, out_dir, name, prompt, images))
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSummary written to {out_dir / 'summary.json'}")
    return 0 if all(item.get("ok") for item in summary) else 1


def run_case(client, args, api_key, out_dir: Path, name: str, prompt: str, images: list[Image.Image]) -> dict:
    payload: dict = {"model": args.model, "prompt": prompt}
    for field, image in zip(IMAGE_FIELDS, images):
        payload[field] = to_data_uri(image, args.max_input_side)
    for key, value in (
        ("num_inference_steps", args.steps),
        ("cfg", args.cfg),
        ("seed", args.seed),
        ("negative_prompt", args.negative_prompt),
    ):
        if value is not None:
            payload[key] = value

    record: dict = {
        "case": name,
        "model": args.model,
        "input_sizes": [list(image.size) for image in images],
        "sent_sizes": [list(downscale(image, args.max_input_side).size) for image in images],
        "prompt_chars": len(prompt),
    }
    print(f"[{name}] requesting {args.model} with {len(images)} image(s) (timeout {args.timeout:.0f}s)...", flush=True)
    started = time.monotonic()
    try:
        response = client.post(
            f"{args.base_url.rstrip('/')}/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Enable-Watermark": "0",
                "X-Trace-Id": f"kigcraft-probe-{name}-{int(time.time())}",
            },
            json=payload,
        )
    except httpx.HTTPError as exc:
        record.update(ok=False, error=f"{exc.__class__.__name__}: {exc}", elapsed_seconds=time.monotonic() - started)
        print(f"[{name}] request failed: {record['error']}")
        return record

    record["elapsed_seconds"] = round(time.monotonic() - started, 2)
    record["status"] = response.status_code
    record["trace_id"] = response.headers.get("x-siliconcloud-trace-id")
    if response.status_code != 200:
        record.update(ok=False, error=response.text[:2000])
        print(f"[{name}] HTTP {response.status_code}: {record['error'][:300]}")
        return record

    body = response.json()
    record["timings"] = body.get("timings")
    record["seed"] = body.get("seed")
    outputs = []
    for index, item in enumerate(body.get("images") or [], start=1):
        image_response = client.get(item["url"])
        image_response.raise_for_status()
        image = Image.open(io.BytesIO(image_response.content))
        path = out_dir / f"{name}-{index}.{(image.format or 'png').lower()}"
        path.write_bytes(image_response.content)
        outputs.append({"path": str(path), "size": list(image.size), "format": image.format})
    record.update(ok=bool(outputs), outputs=outputs)
    print(f"[{name}] inputs={record['sent_sizes']} -> outputs={[o['size'] for o in outputs]} in {record['elapsed_seconds']}s")
    return record


def downscale(image: Image.Image, max_side: int) -> Image.Image:
    if max(image.size) <= max_side:
        return image
    scale = max_side / max(image.size)
    return image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)


def to_data_uri(image: Image.Image, max_side: int) -> str:
    buffer = io.BytesIO()
    downscale(image, max_side).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def pad_to_ratio(image: Image.Image, ratio: float) -> Image.Image:
    width, height = image.size
    if width / height < ratio:
        canvas_size = (round(height * ratio), height)
    else:
        canvas_size = (width, round(width / ratio))
    canvas = Image.new("RGB", canvas_size, "white")
    canvas.paste(image, ((canvas_size[0] - width) // 2, (canvas_size[1] - height) // 2))
    return canvas


if __name__ == "__main__":
    raise SystemExit(main())
