"""Training pipeline (§28).

    python -m ai.datasets.build          --version v0.1.0
    python -m ai.training.validate_dataset --config ai/configs/train_8b.yaml
    python -m ai.training.prepare_dataset  --config ai/configs/train_8b.yaml --inspect 2
    python -m ai.training.train_lora       --config ai/configs/train_8b.yaml
    python -m ai.training.merge_adapter    --run kase-ai-8b-v0.1
    python -m ai.training.export_model     --run kase-ai-8b-v0.1 --format gguf
    python -m ai.evaluation.evaluate       --label kase-ai-8b-v0.1 --runtime vllm
    python -m ai.training.registry promote kase-ai-8b-v0.1 \\
        --baseline rules-tools-rag --candidate kase-ai-8b-v0.1
"""

from ai.training.config import TrainingConfig, load_training_config
from ai.training.registry import list_models, read_metadata, write_metadata

__all__ = [
    "TrainingConfig",
    "list_models",
    "load_training_config",
    "read_metadata",
    "write_metadata",
]
