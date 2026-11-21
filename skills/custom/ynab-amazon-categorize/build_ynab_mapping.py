#!/usr/bin/env python3
"""Build an entity-ID category update plan from categorized Amazon purchases."""
import argparse
import csv
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("categorization_csv")
    parser.add_argument("preflight_json")
    parser.add_argument("output_json")
    args = parser.parse_args()

    with open(args.preflight_json) as source:
        preflight = json.load(source)
    transactions = {transaction["entity_id"]: transaction for transaction in preflight["transactions"]}
    categories = {(category["group"], category["name"]): category for category in preflight["categories"]}

    plan, errors = [], []
    with open(args.categorization_csv, newline="") as source:
        for line, row in enumerate(csv.DictReader(source), start=2):
            entity_id = row.get("entity_id", "")
            target = categories.get((row.get("proposed_category_group", ""), row.get("proposed_category", "")))
            if not entity_id or entity_id not in transactions:
                errors.append(f"line {line}: unknown entity_id {entity_id!r}")
            elif not target:
                errors.append(f"line {line}: unknown category {row.get('proposed_category_group')} / {row.get('proposed_category')}")
            else:
                plan.append({
                    "entity_id": entity_id,
                    "date": transactions[entity_id]["date"],
                    "outflow": transactions[entity_id]["outflow"],
                    "current_category": transactions[entity_id]["category"],
                    "category_id": target["entity_id"],
                    "category_group": target["group"],
                    "category_name": target["name"],
                    "confidence": row.get("confidence", "medium"),
                })
    if errors:
        raise SystemExit("Invalid categorization:\n" + "\n".join(errors))

    output = {"scope": preflight["scope"], "transactions": plan}
    with open(args.output_json, "w") as destination:
        json.dump(output, destination, indent=2)
    print(f"Prepared {len(plan)} entity-ID category updates")


if __name__ == "__main__":
    main()
