---
name: ynab-amazon-categorize
description: "Categorize scoped Amazon transactions in a YNAB budget from real Amazon orders. Use when matching Amazon purchases to YNAB categories through CDP without clicking."
---

# YNAB Amazon Categorizer

Use live YNAB transactions as the source of truth, extract only the required Amazon order history, categorize matched orders in parallel, then apply an entity-ID update plan. Do not click UI controls or use screenshots.

## Prerequisites

- `browser-use` can reach Chrome or Chromium. If autodiscovery fails, use the browser's displayed DevTools address: `export BU_CDP_URL=http://127.0.0.1:PORT`.
- Logged-in `amazon.com` and `app.ynab.com` tabs are available in the same browser.
- Use `cdp("Runtime.evaluate", ...)`, not `js()`, for Amazon and YNAB.

## Workflow

Set the skill location once:

```bash
SKILL_DIR=~/.config/opencode/skills/ynab-amazon-categorize
```

### 1. Preflight YNAB First

Scope the transaction set directly from YNAB. Default to the most recent 31 days; use explicit dates when the user specifies a period.

```bash
python3 "$SKILL_DIR/preflight_ynab.py" data/ynab_preflight.json \
  --start-date 2026-07-01 --end-date 2026-07-31
```

Output contains the exact Amazon transaction `entity_id`, live category, scoped date range, and category IDs. It is the source of truth for every subsequent step. Do not use a CSV to decide which YNAB transactions to change.

### 2. Extract Only Scoped Amazon Orders

```bash
python3 "$SKILL_DIR/extract_amazon_orders.py" \
  data/amazon_orders_raw.json --preflight data/ynab_preflight.json
```

The extractor uses the preflight start date to stop pagination once it reaches older orders and filters output to the preflight date range. Increase `--pages` only if the matching orders are not found.

### 3. Match Scoped Transactions

```bash
python3 "$SKILL_DIR/match_ynab_amazon.py" \
  data/ynab_preflight.json data/amazon_orders_raw.json data/amazon_matched.json
```

Matching is exact by amount and chooses the closest order date. Preserve each transaction's `entity_id`. Unmatched records are not eligible for category updates.

### 4. Split Matched Transactions

Create batches from records whose `match_status` is `matched`; never send unmatched records to categorization.

```bash
python3 -c "import json; data=json.load(open('data/amazon_matched.json')); data=[row for row in data if row['match_status']=='matched']; [json.dump(data[i:i+10], open(f'data/amazon_batch_{i//10+1}.json','w'), indent=2) for i in range(0,len(data),10)]"
```

Dispatch all batch files as parallel sub-agents. Each agent must write `data/amazon_batch_N_categorized.json` with every source row represented and these fields:

```json
{
  "entity_id": "YNAB transaction entity ID from the batch",
  "ynab_date": "MM/DD/YYYY",
  "outflow": 31.15,
  "current_category": "Miscellaneous",
  "amazon_order_date": "July 9, 2026",
  "amazon_order_total": "$31.15",
  "order_items": "item1; item2",
  "proposed_category_group": "True Expenses",
  "proposed_category": "Household Necessities",
  "confidence": "high"
}
```

Use only categories from `ynab_preflight.json`'s category list. Set confidence to `high`, `medium`, or `low`. Categorize uncertain items as `Miscellaneous`. Flag low-confidence recommendations and any recommendation that changes a live non-Miscellaneous category in the summary for user review.

### 5. Build a Validated Entity-ID Plan

```bash
python3 "$SKILL_DIR/merge_categorized.py" \
  data/amazon_categorization.csv data/amazon_batch_*_categorized.json

python3 "$SKILL_DIR/build_ynab_mapping.py" \
  data/amazon_categorization.csv data/ynab_preflight.json data/ynab_update_mapping.json
```

The builder validates both category group and category name against the live YNAB catalog and creates an entity-ID plan. It rejects unknown IDs or categories instead of guessing. This prevents collisions between transactions that share a date and amount.

### 6. Dry Run, Then Apply

Always run the dry run and summarize it before applying:

```bash
python3 "$SKILL_DIR/update_ynab_categories.py" data/ynab_update_mapping.json
```

After the user approves the proposed changes, apply exactly that entity-ID plan:

```bash
python3 "$SKILL_DIR/update_ynab_categories.py" \
  data/ynab_update_mapping.json --apply
```

The updater opens YNAB, verifies every target remains an Amazon transaction, and reports `CHANGED`, `WOULD_CHANGE`, `ALREADY_SET`, or `ERROR`. Transactions outside the plan are never reported as missing or modified.

## Technical Notes

- YNAB entity manager: `window.ynab.YNABSharedLibWebInstance._firstInstanceCreated._entityManager`.
- YNAB amount is milliunits: `Math.abs(txn.amount) / 1000`.
- `String(txn.date)` is `YYYY-MM-DD`; it is not a native `Date`.
- Apply categories through `inst._changeSetManager.performAsSingleChangeSet(() => { txn.subCategoryId = categoryId; })` so YNAB tracks and syncs the change.
- The scripts inherit `BU_CDP_URL`; set it when browser-use cannot auto-discover Chromium's dynamic debug port.

## Files

- `preflight_ynab.py`: live YNAB scope, transaction IDs, and category catalog.
- `extract_amazon_orders.py`: date-bounded Amazon extraction.
- `match_ynab_amazon.py`: entity-preserving exact amount matching.
- `merge_categorized.py`: batch JSON to CSV merge.
- `build_ynab_mapping.py`: validates category proposals and builds entity-ID update plans.
- `update_ynab_categories.py`: dry-run-by-default, entity-ID YNAB updater.
