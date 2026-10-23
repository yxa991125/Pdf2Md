from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - the production worker runs on Linux
    resource = None  # type: ignore[assignment]


CORE_PAGES = [49, 50, 160, 161, 162, 194, 195]


def main() -> int:
    args = _arguments().parse_args()
    output = Path(args.output).resolve()
    native_dir = output / "native_output"
    markdown_dir = output / "markdown"
    assets_dir = output / "assets"
    for directory in (native_dir, markdown_dir, assets_dir):
        directory.mkdir(parents=True, exist_ok=True)
    pages = list(range(1, args.page_count + 1)) if args.mode == "full" else [int(value) for value in args.pages.split(",")]
    started = time.perf_counter()
    usage_before = _usage()
    base_manifest: dict[str, Any] = {
        "parser": args.parser,
        "mode": args.mode,
        "pages": pages,
        "page_mapping": {str(page): page for page in pages},
        "python": platform.python_version(),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "expected_gpu_uuid": args.expected_gpu_uuid,
        "network": "unshared" if os.environ.get("S1_NETWORK_ISOLATED") == "1" else "not_os_verified",
        "chunking": "one original PDF page per inference call",
        "retry_count": 0,
    }
    try:
        if base_manifest["cuda_visible_devices"] != args.expected_gpu_uuid:
            raise RuntimeError("CUDA_VISIBLE_DEVICES does not equal the authorized MIG UUID")
        if args.parser == "b3":
            version, native, markdown, gpu = _run_b3(Path(args.input), output, pages, Path(args.model_path))
        elif args.parser == "b4":
            version, native, markdown, gpu = _run_b4(Path(args.input), output, pages, Path(args.model_path))
        else:
            raise ValueError(args.parser)
        markdown_path = markdown_dir / "document.md"
        markdown_path.write_text(_normalize_newlines(markdown), encoding="utf-8")
        native_path = native_dir / "native.json"
        native_path.write_text(json.dumps(native, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
        elapsed = time.perf_counter() - started
        usage_after = _usage()
        cpu_seconds = (usage_after.ru_utime + usage_after.ru_stime) - (usage_before.ru_utime + usage_before.ru_stime)
        manifest = {
            **base_manifest,
            "status": "success",
            "parser_version": version,
            "framework": gpu["framework"],
            "framework_version": gpu["framework_version"],
            "cuda_runtime": gpu["cuda_runtime"],
            "device_name": gpu["device_name"],
            "compute_capability": gpu.get("compute_capability"),
            "gpu_compute_verified": gpu["gpu_compute_verified"],
            "gpu_memory_peak_mib": gpu.get("gpu_memory_peak_mib"),
            "gpu_memory_after_release_mib": gpu.get("gpu_memory_after_release_mib"),
            "models": gpu["models"],
            "wall_seconds": round(elapsed, 6),
            "cpu_seconds": round(cpu_seconds, 6),
            "cpu_percent": round(cpu_seconds / elapsed * 100, 2) if elapsed else 0.0,
            "peak_rss_kib": _usage().ru_maxrss,
            "markdown_sha256": _sha256(markdown_path),
            "native_sha256": _sha256(native_path),
        }
        _write_manifest(native_dir, manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0
    except Exception as exc:
        elapsed = time.perf_counter() - started
        error_type = _classify_error(exc)
        failure = {
            **base_manifest,
            "status": "failed",
            "error_type": error_type,
            "exception_type": type(exc).__name__,
            "error": str(exc),
            "wall_seconds": round(elapsed, 6),
            "peak_rss_kib": _usage().ru_maxrss,
        }
        _write_manifest(native_dir, failure)
        (native_dir / "failure_traceback.log").write_text(traceback.format_exc(), encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2


def _arguments() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parser", required=True, choices=["b3", "b4"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", required=True, choices=["core", "full"])
    parser.add_argument("--pages", default=",".join(map(str, CORE_PAGES)))
    parser.add_argument("--page-count", type=int, default=257)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--expected-gpu-uuid", required=True)
    return parser


def _run_b3(
    pdf_path: Path,
    output: Path,
    pages: list[int],
    model_path: Path,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    runtime_model_base = output / "native_output" / "mineru_runtime_models"
    runtime_model_base.mkdir(parents=True, exist_ok=True)
    repo_name = "MinerU-4_models_torch"
    source_repo = model_path / repo_name
    if not source_repo.is_dir():
        raise FileNotFoundError(f"MinerU model repository missing: {source_repo}")
    runtime_repo = runtime_model_base / repo_name
    if not runtime_repo.exists():
        runtime_repo.symlink_to(source_repo, target_is_directory=True)
    os.environ.update({
        "MINERU_MODEL_BASE_DIR": str(runtime_model_base),
        "MINERU_MODEL_SOURCE": "local",
        "MINERU_MODEL_SMALL_BACKEND": "torch",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    import torch
    from mineru.parser import MinerUParser
    from mineru.parser.writer import FileBasedDataWriter

    gpu = _torch_gpu_probe(torch)
    parser = MinerUParser(tier="basic", parse_mode="auto", image_analysis=True)
    page_records: list[dict[str, Any]] = []
    markdown_parts: list[str] = []
    for page in pages:
        page_root = output / "native_output" / "pages" / f"page-{page:03d}"
        result = parser.parse(pdf_path, page_range=str(page))
        result.save(FileBasedDataWriter(str(page_root)))
        page_markdown = (page_root / "markdown.md").read_text(encoding="utf-8")
        page_markdown = _materialize_page_assets(page_markdown, page_root, output / "assets" / f"page-{page:03d}")
        markdown_parts.append(f"<!-- PAGE {page} -->\n\n{page_markdown}")
        page_records.append({
            "page": page,
            "middle_json": (page_root / "middle_json.json").relative_to(output).as_posix(),
            "structured_content": (page_root / "structured_content.json").relative_to(output).as_posix(),
            "model_output": (
                (page_root / "model_output.json").relative_to(output).as_posix()
                if (page_root / "model_output.json").is_file() else None
            ),
        })
    gpu.update(_torch_gpu_finish(torch))
    gpu["models"] = _model_inventory(model_path)
    return importlib.metadata.version("mineru"), {"pages": page_records}, "\n\n".join(markdown_parts), gpu


def _run_b4(
    pdf_path: Path,
    output: Path,
    pages: list[int],
    model_path: Path,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    import numpy as np
    import paddle
    import pypdfium2 as pdfium
    from paddleocr import PPStructureV3

    gpu = _paddle_gpu_probe(paddle)
    dirs = {name: model_path / name for name in _paddle_model_names()}
    missing = [name for name, path in dirs.items() if not path.is_dir()]
    if missing:
        raise FileNotFoundError(f"PaddleOCR model directories missing: {missing}")
    pipeline = PPStructureV3(
        device="gpu:0",
        layout_detection_model_dir=str(dirs["PP-DocLayout_plus-L"]),
        text_detection_model_dir=str(dirs["PP-OCRv5_server_det"]),
        text_recognition_model_dir=str(dirs["PP-OCRv5_server_rec"]),
        table_classification_model_dir=str(dirs["PP-LCNet_x1_0_table_cls"]),
        table_orientation_classify_model_dir=str(dirs["PP-LCNet_x1_0_doc_ori"]),
        wired_table_structure_recognition_model_dir=str(dirs["SLANeXt_wired"]),
        wireless_table_structure_recognition_model_dir=str(dirs["SLANet_plus"]),
        wired_table_cells_detection_model_dir=str(dirs["RT-DETR-L_wired_table_cell_det"]),
        wireless_table_cells_detection_model_dir=str(dirs["RT-DETR-L_wireless_table_cell_det"]),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        use_seal_recognition=False,
        use_table_recognition=True,
        use_formula_recognition=False,
        use_chart_recognition=False,
        use_region_detection=False,
        format_block_content=False,
    )
    document = pdfium.PdfDocument(str(pdf_path))
    page_records: list[dict[str, Any]] = []
    markdown_parts: list[str] = []
    native_pages = output / "native_output" / "pages"
    native_pages.mkdir(parents=True, exist_ok=True)
    for page in pages:
        rendered = document[page - 1].render(scale=200 / 72).to_pil().convert("RGB")
        results = list(
            pipeline.predict(
                input=np.asarray(rendered),
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                use_seal_recognition=False,
                use_table_recognition=True,
                use_formula_recognition=False,
                use_chart_recognition=False,
                use_region_detection=False,
                use_table_orientation_classify=False,
                # PaddleX 3.7.2 otherwise calls an uninitialized nested OCR
                # model on some wired tables when the parent supplies OCR.
                use_ocr_results_with_table_cells=False,
            )
        )
        if len(results) != 1:
            raise RuntimeError(f"PaddleOCR page {page} returned {len(results)} results; expected 1")
        result = results[0]
        native_path = native_pages / f"page-{page:03d}.json"
        native_path.write_text(json.dumps(result.json["res"], ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")
        markdown_data = result.markdown
        page_markdown = str(markdown_data.get("markdown_texts", ""))
        page_markdown = _save_paddle_assets(markdown_data, page_markdown, output / "assets" / f"page-{page:03d}")
        markdown_parts.append(f"<!-- PAGE {page} -->\n\n{page_markdown}")
        page_records.append({
            "page": page,
            "page_index": result.json["res"].get("page_index"),
            "native_json": native_path.relative_to(output).as_posix(),
        })
        del result, results, rendered
    document.close()
    gpu.update(_paddle_gpu_finish(paddle))
    gpu["models"] = _model_inventory(model_path)
    return importlib.metadata.version("paddleocr"), {"pages": page_records}, "\n\n".join(markdown_parts), gpu


def _torch_gpu_probe(torch: Any) -> dict[str, Any]:
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError(f"PyTorch expected exactly one visible CUDA device, got {torch.cuda.device_count()}")
    torch.cuda.set_device(0)
    before = torch.cuda.memory_allocated(0)
    left = torch.randn((1024, 1024), device="cuda")
    right = torch.randn((1024, 1024), device="cuda")
    product = left @ right
    torch.cuda.synchronize()
    verified = bool(torch.isfinite(product).all().item())
    peak = torch.cuda.max_memory_allocated(0)
    return {
        "framework": "pytorch",
        "framework_version": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "device_name": torch.cuda.get_device_name(0),
        "compute_capability": list(torch.cuda.get_device_capability(0)),
        "gpu_compute_verified": verified,
        "gpu_memory_before_probe_mib": round(before / 1048576, 3),
        "gpu_memory_probe_peak_mib": round(peak / 1048576, 3),
    }


def _torch_gpu_finish(torch: Any) -> dict[str, Any]:
    peak = torch.cuda.max_memory_allocated(0)
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
    return {
        "gpu_memory_peak_mib": round(peak / 1048576, 3),
        "gpu_memory_after_release_mib": round(torch.cuda.memory_allocated(0) / 1048576, 3),
    }


def _paddle_gpu_probe(paddle: Any) -> dict[str, Any]:
    if not paddle.device.is_compiled_with_cuda() or paddle.device.cuda.device_count() != 1:
        raise RuntimeError(f"PaddlePaddle expected exactly one visible CUDA device, got {paddle.device.cuda.device_count()}")
    paddle.device.set_device("gpu:0")
    left = paddle.randn([1024, 1024])
    right = paddle.randn([1024, 1024])
    product = paddle.matmul(left, right)
    value = float(paddle.sum(product).numpy())
    if not (value == value and abs(value) != float("inf")):
        raise RuntimeError("PaddlePaddle CUDA matrix multiplication returned a non-finite value")
    return {
        "framework": "paddlepaddle",
        "framework_version": paddle.__version__,
        "cuda_runtime": paddle.version.cuda(),
        "device_name": paddle.device.cuda.get_device_name(0),
        "compute_capability": None,
        "gpu_compute_verified": True,
    }


def _paddle_gpu_finish(paddle: Any) -> dict[str, Any]:
    try:
        peak = paddle.device.cuda.max_memory_allocated(0)
        paddle.device.cuda.empty_cache()
        after = paddle.device.cuda.memory_allocated(0)
        return {
            "gpu_memory_peak_mib": round(peak / 1048576, 3),
            "gpu_memory_after_release_mib": round(after / 1048576, 3),
        }
    except Exception:
        return {"gpu_memory_peak_mib": None, "gpu_memory_after_release_mib": None}


def _paddle_model_names() -> list[str]:
    return [
        "PP-DocLayout_plus-L",
        "PP-OCRv5_server_det",
        "PP-OCRv5_server_rec",
        "PP-LCNet_x1_0_table_cls",
        "PP-LCNet_x1_0_doc_ori",
        "SLANeXt_wired",
        "SLANet_plus",
        "RT-DETR-L_wired_table_cell_det",
        "RT-DETR-L_wireless_table_cell_det",
    ]


def _materialize_page_assets(markdown: str, page_root: Path, asset_root: Path) -> str:
    for path in sorted(page_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() in {".md", ".json"}:
            continue
        relative = path.relative_to(page_root)
        destination = asset_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        markdown = markdown.replace(relative.as_posix(), f"../assets/{asset_root.name}/{relative.as_posix()}")
    return markdown


def _save_paddle_assets(data: dict[str, Any], markdown: str, asset_root: Path) -> str:
    from PIL import Image

    asset_root.mkdir(parents=True, exist_ok=True)
    for value in data.values():
        if not isinstance(value, dict):
            continue
        for raw_name, image in value.items():
            if image is None:
                continue
            name = Path(str(raw_name)).name
            destination = asset_root / name
            if hasattr(image, "save"):
                image.save(destination)
            else:
                Image.fromarray(image).save(destination)
            markdown = markdown.replace(str(raw_name).replace("\\", "/"), f"../assets/{asset_root.name}/{name}")
    return markdown


def _model_inventory(root: Path) -> dict[str, Any]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".cache" not in path.parts:
            files.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": _sha256(path)})
    digest = hashlib.sha256()
    for item in files:
        digest.update(f"{item['path']}\0{item['sha256']}\n".encode("utf-8"))
    return {"root": str(root), "file_count": len(files), "inventory_sha256": digest.hexdigest(), "files": files}


def _classify_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}".casefold()
    if "out of memory" in message or "oom" in message:
        return "gpu_oom"
    if "model" in message and any(term in message for term in ("missing", "load", "not found")):
        return "model_load_failure"
    if "page" in message and any(term in message for term in ("missing", "returned 0", "expected")):
        return "missing_page"
    if "cuda" in message or "gpu" in message:
        return "gpu_execution_failure"
    return "parser_failure"


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _write_manifest(native_dir: Path, payload: dict[str, Any]) -> None:
    (native_dir / "worker_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8"
    )


class _EmptyUsage:
    ru_utime = 0.0
    ru_stime = 0.0
    ru_maxrss = 0


def _usage() -> Any:
    if resource is None:
        return _EmptyUsage()
    return resource.getrusage(resource.RUSAGE_SELF)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
