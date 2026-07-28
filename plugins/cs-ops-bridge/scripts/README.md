# migrate_experience_to_knowledge.py

One-shot migration tool that copies **reusable product/policy facts** from the
Experience bank (`povison-cs-hermes-user` @ `192.168.10.63:8888`) into the Knowledge
bank (`furniture-knowledge` @ `192.168.10.123:8888). **Add-only — NEVER deletes from the
source bank** (deleting source memories triggers a consolidation cascade, see
`hindsight-operation-pitfalls.md`).

## Why

The Experience bank historically captured both agent-operational session notes AND some
reusable product/policy facts. Under bank isolation, only product/policy facts belong in
the Knowledge bank. This tool surfaces those facts and re-classifies them into the new
dual-domain schema (`domain`, `product_id`, `policy_type`, typed entities, new tags).

## Usage

```bash
# Dry-run (no writes) — inspect what would migrate
python3 scripts/migrate_experience_to_knowledge.py --limit 50 --dry-run

# Live migrate a window (add-only, async retain)
python3 scripts/migrate_experience_to_knowledge.py --limit 200 --offset 0 --source-tag human_confirmed

# Filter by fact type
python3 scripts/migrate_experience_to_knowledge.py --limit 100 --type world

# After a batch: trigger consolidation, then verify
curl -X POST http://192.168.10.123:8888/v1/default/banks/furniture-knowledge/consolidate -d '{}'
```

Flags: `--limit`, `--offset`, `--type` (world|experience|observation), `--state`,
`--dry-run`, `--source-tag` (metadata `source` value for migrated facts, default `user_reported`).

## Filtering (skip non-reusable)

An item is skipped if any of:
- **agent_narrative** — agent-operational process notes (draft-save, apply-handoff, pii_flag,
  bridge CLI, terminal tool, fabrication rules, intent classifier, etc.). Content-based — a
  fact may carry a `session:*` origin tag and still be reusable product knowledge.
- **handling_experience** — customer-emotion management / response strategy (安抚, 急躁, 致歉,
  apologize, soothe, de-escalate). These belong in the Experience bank, NOT the Knowledge bank.
- **one_off_compensation** — goodwill dollar amounts / order-specific credits.
- **residual_pii** — PII remains after heuristic redaction.
- **empty**.

## Re-classification

- `domain` inferred from text + entities + source `product_name:*`/`product_material:*` tags.
- `product_id` (SKU) extracted from free text — supports hyphenated (`M2-SF8248`) and
  non-hyphenated (`DT8366DD150`) forms.
- `product_name` taken from source `product_name:*` tag.
- `policy_type` matched against the extended enum (return|warranty|shipping|installation|payment|swatch).
- PII is heuristic-redacted before retain; `evidence_doc` records `migrated_from:<source_id>`.

## Validation (performed 2026-07-28)

Sample run (`--limit 30`): 2 product facts (DT8366DD150 Dura dining table) retained,
28 skipped (26 agent_narrative + 2 handling_experience). After `POST .../consolidate`,
`knowledge_recall(question="DT8366DD150...", sku="DT8366DD150")` returns the migrated fact
with `metadata_filter={domain:product, product_id:DT8366DD150}`. Pipeline validated
end-to-end: list → filter → refine → retain → consolidate → recall-with-filter.

## Full-scale migration (follow-up)

The Experience bank has ~26600 items. A full migration requires paginating the whole bank
(loop `--offset` in 200-item windows), optionally LLM-assisted refine for ambiguous domain
classification, and polling `GET .../operations` + triggering `POST .../consolidate` per
batch until stable. Run in a background session; do NOT bulk-DELETE the source bank.

---

# review_unknown_attributes.py

Periodic human-review tool for the reference attribute vocabulary (plan P0.5:
「解析时优先映射已知属性名，未知原样透传并标记，定期人工 review 并入」).

`knowledge_recall`'s Parser normalizes known attributes against
`hindsight_attribute_vocab.json`; unknown attributes pass through unchanged and are
flagged (`attribute_known=False` in the parsed output). Over time the Knowledge bank
accumulates attribute values not yet in the vocab. This script surfaces them so a human
can promote stable, high-frequency ones into the canonical vocabulary.

## Usage

```bash
# Scan default bank (limit 1000 memories), list known vs unknown attributes
python3 scripts/review_unknown_attributes.py

# Scan a larger window
python3 scripts/review_unknown_attributes.py --limit 5000

# Emit a draft vocab snippet for the unknown candidates (paste into vocab after review)
python3 scripts/review_unknown_attributes.py --apply-draft
```

The script does NOT mutate the bank or the vocab file. `--apply-draft` only prints a
JSON snippet to stdout. After review, paste approved entries into
`hindsight_attribute_vocab.json` and re-run to confirm coverage improves.

## Workflow

1. Run monthly (or when recall quality on attribute queries degrades).
2. Inspect the unknown list — drop noise (PII fragments, one-offs), keep stable concepts.
3. Add approved entries to `hindsight_attribute_vocab.json` with canonical `attribute` +
   `synonyms` + `category`.
4. Re-run to confirm the unknown list shrinks.
