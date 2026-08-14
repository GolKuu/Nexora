# Feedback → review → next version (§63, §64)

Production feedback is the best source of training data this project has, and
the most dangerous. An answer marked "не полезно" may be wrong, or the user may
simply have disliked a correct refusal. Auto-training on the raw signal would
teach the model to stop refusing — the exact failure mode the dataset is built
to prevent.

So: **nothing trains automatically.** Every item passes a human.

## The loop

```
production                      var/ai-review-queue.jsonl
  POST /ai/feedback   ------->  status: pending_human_review
                                          |
                                     human review
                                          |
              +---------------------------+---------------------------+
              |                           |                           |
       not a defect              corrected sample              product bug
    status: dismissed         status: accepted, with a       -> issue against
                              corrected assistant turn          the engine
                                          |
                       ai/datasets/builders/reviewed.py picks up
                       accepted items into the next dataset version
                                          |
                        rebuild -> validate -> train -> benchmark
                                          |
                                    release gate (§65)
```

## Reviewing an item

Open `var/ai-review-queue.jsonl`. For each `pending_human_review` entry decide:

1. **Was the answer actually wrong?** Check the figures against the engine:
   `python -m ai.tools.executors` equivalents via `/ai/tool`. A number that
   matches the engine is not a model defect — if it is wrong, the *engine* is
   wrong and that is a normal bug, not training data.
2. **Which failure is it?**
   - wrong tool or wrong arguments → `tool_call` sample;
   - invented data → `refusal` sample (this is the highest-value category);
   - correct but unclear → `bond_explanation` / `simple_language` sample;
   - correct and clear, user disagreed → `dismissed`, with a note.
3. **Write the corrected assistant turn yourself.** Do not paste a corrected
   answer produced by another model: the numbers must come from our engine and
   the wording must be a human's.
4. Set `status` to `accepted` or `dismissed`, add `reviewer` and `corrected`.

## Promotion rules

- A single reviewer may accept at most 20 items per dataset version; beyond
  that, a second reviewer signs off. One person's stylistic preference should
  not become the model's voice.
- Items whose corrected answer contains a figure the engine cannot reproduce
  are rejected outright (§59).
- Accepted items are added with `synthetic=false` and
  `provenance.source = "production_review"`, so their share of the corpus is
  visible in the quality report.
- A dataset version that draws more than 15% of its samples from review is
  suspect: it means the generators are not covering something, and the fix
  belongs in the builder, not in the queue.

## Cadence

Review weekly. Rebuild and retrain when either 100 items have accumulated or a
new KASE snapshot lands — whichever comes first. Every retrain ends at the
release gate; a model that fails it stays out of production regardless of how
much review work went into its data.
