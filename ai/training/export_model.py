"""Export merged weights for a serving runtime (§55).

    python -m ai.training.export_model --run kase-ai-8b-v0.1 --format gguf --quant Q4_K_M
    python -m ai.training.export_model --run kase-ai-8b-v0.1 --format awq

Quantization is allowed only with a measured comparison (§55): the exporter
writes a ``quantization.json`` next to the artefact that records what still
needs to be benchmarked, and prints the exact command to do it. Shipping a
4-bit model because it fits is how a bond assistant starts making arithmetic
mistakes nobody attributed to the export step.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ai import _bootstrap

MODELS_ROOT = _bootstrap.REPO_ROOT / "models"

GGUF_QUANTS = ("F16", "Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q4_0")


def export_gguf(run: str, quant: str = "Q4_K_M", llama_cpp: str | None = None) -> Path:
    """Convert to GGUF for llama.cpp (CPU / small-GPU serving)."""
    if quant not in GGUF_QUANTS:
        raise SystemExit(f"неизвестная квантизация {quant!r}, доступны: {GGUF_QUANTS}")
    run_dir = MODELS_ROOT / run
    merged = run_dir / "merged"
    if not merged.exists():
        raise SystemExit(f"{merged} не найден — сначала python -m ai.training.merge_adapter --run {run}")

    tools = Path(llama_cpp) if llama_cpp else Path("llama.cpp")
    converter = tools / "convert_hf_to_gguf.py"
    if not converter.exists():
        raise SystemExit(
            f"не найден {converter}. Склонируйте llama.cpp и укажите путь через --llama-cpp:\n"
            f"    git clone https://github.com/ggerganov/llama.cpp"
        )

    target_dir = run_dir / "gguf"
    target_dir.mkdir(parents=True, exist_ok=True)
    f16 = target_dir / f"{run}-F16.gguf"
    subprocess.run(
        ["python", str(converter), str(merged), "--outfile", str(f16), "--outtype", "f16"],
        check=True,
    )
    if quant == "F16":
        target = f16
    else:
        quantizer = shutil.which("llama-quantize") or str(tools / "llama-quantize")
        target = target_dir / f"{run}-{quant}.gguf"
        subprocess.run([quantizer, str(f16), str(target), quant], check=True)

    _write_quantization_note(run_dir, target, fmt="gguf", quant=quant)
    return target


def export_awq(run: str, bits: int = 4, group_size: int = 128) -> Path:
    """AWQ quantization for vLLM serving."""
    from ai.training.config import require

    require("awq", extra="pip install autoawq")
    from awq import AutoAWQForCausalLM
    from transformers import AutoTokenizer

    run_dir = MODELS_ROOT / run
    merged = run_dir / "merged"
    target = run_dir / f"awq-{bits}bit"
    tokenizer = AutoTokenizer.from_pretrained(str(merged))
    model = AutoAWQForCausalLM.from_pretrained(str(merged))
    model.quantize(
        tokenizer,
        quant_config={"zero_point": True, "q_group_size": group_size, "w_bit": bits, "version": "GEMM"},
    )
    model.save_quantized(str(target))
    tokenizer.save_pretrained(str(target))
    _write_quantization_note(run_dir, target, fmt="awq", quant=f"{bits}bit-g{group_size}")
    return target


def _write_quantization_note(run_dir: Path, artefact: Path, *, fmt: str, quant: str) -> None:
    note = {
        "format": fmt,
        "quantization": quant,
        "artefact": str(artefact.relative_to(_bootstrap.REPO_ROOT)),
        "bytes": artefact.stat().st_size if artefact.is_file() else sum(
            f.stat().st_size for f in artefact.rglob("*") if f.is_file()
        ),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "benchmarked": False,
        "required_before_production": (
            "Квантизация не считается принятой, пока не измерена (§55). Запустите:\n"
            f"  python -m ai.evaluation.evaluate --label {run_dir.name}-{quant} "
            f"--runtime {'llama_cpp' if fmt == 'gguf' else 'vllm'}\n"
            f"  python -m ai.evaluation.compare_models --gate "
            f"--baseline {run_dir.name} --candidate {run_dir.name}-{quant}"
        ),
    }
    (run_dir / f"quantization-{fmt}-{quant}.json").write_text(
        json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(note, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a trained model for serving")
    parser.add_argument("--run", required=True)
    parser.add_argument("--format", choices=("gguf", "awq"), default="gguf")
    parser.add_argument("--quant", default="Q4_K_M")
    parser.add_argument("--bits", type=int, default=4)
    parser.add_argument("--llama-cpp", default=None)
    args = parser.parse_args()

    if args.format == "gguf":
        export_gguf(args.run, quant=args.quant, llama_cpp=args.llama_cpp)
    else:
        export_awq(args.run, bits=args.bits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
