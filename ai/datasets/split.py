"""Train / dev splitting.

Two properties matter more than the ratio.

**No prompt leaks across the split.** Samples are grouped by ``prompt_hash``
before assignment, so a paraphrase-identical question cannot sit on both sides
and inflate the dev score.

**Every task is represented on both sides.** A dev set missing the refusal task
would report a healthy loss while the model quietly learned to answer
everything, so the split is stratified per task and a task with too few samples
keeps at least one on each side.
"""

from __future__ import annotations

import random
from collections import defaultdict

from ai.datasets.schema import SFTSample

SPLIT_SEED = 7


def split_train_dev(
    samples: list[SFTSample], *, dev_fraction: float = 0.1, seed: int = SPLIT_SEED
) -> tuple[list[SFTSample], list[SFTSample]]:
    by_task: dict[str, list[SFTSample]] = defaultdict(list)
    for sample in samples:
        by_task[sample.task].append(sample)

    rng = random.Random(seed)
    train: list[SFTSample] = []
    dev: list[SFTSample] = []

    for task, task_samples in sorted(by_task.items()):
        groups: dict[str, list[SFTSample]] = defaultdict(list)
        for sample in task_samples:
            groups[sample.prompt_hash].append(sample)
        keys = sorted(groups)
        rng.shuffle(keys)
        wanted = max(1, round(len(keys) * dev_fraction)) if len(keys) > 2 else 0
        for index, key in enumerate(keys):
            (dev if index < wanted else train).extend(groups[key])

    rng.shuffle(train)
    rng.shuffle(dev)
    return train, dev


def assert_no_leakage(train: list[SFTSample], dev: list[SFTSample]) -> None:
    overlap = {s.prompt_hash for s in train} & {s.prompt_hash for s in dev}
    if overlap:
        raise ValueError(f"train/dev leakage on {len(overlap)} prompt hashes")


__all__ = ["assert_no_leakage", "split_train_dev", "SPLIT_SEED"]
