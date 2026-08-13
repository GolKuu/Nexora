"""KASE Bond AI - our own AI system.

The product's primary intelligence is a model we fine-tune, version and serve
on our own infrastructure. No closed third-party LLM API sits on the answer
path, and none is used as a fallback when our model is unsure (§61).

Layout
------
ai/datasets    collection, cleaning, parsing, chunking, dataset building
ai/tools       the deterministic functions the model is allowed to call
ai/prompts     versioned system prompts and chat templates
ai/embeddings  open-weight embedder (+ offline deterministic fallback)
ai/retrieval   index, store, query understanding, context builder, reranker
ai/training    prepare / validate / train / merge / export / registry
ai/evaluation  golden set, metrics, benchmark runner, model comparison
ai/inference   runtime abstraction, agent loop, safety, HTTP service
ai/models      -> repository root models/ (checkpoints + model cards)
"""

__version__ = "0.1.0"

#: Bumped whenever the meaning of a stored dataset/index/answer changes.
DATASET_VERSION = "v0.1.0"
INDEX_VERSION = "v0.1.0"
MODEL_VERSION = "kase-ai-v0.1"
