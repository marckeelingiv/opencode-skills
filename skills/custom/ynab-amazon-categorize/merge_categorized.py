#!/usr/bin/env python3
"""Merge categorized batch JSON files into a single CSV.

Usage:
    python3 merge_categorized.py <output_csv> <batch1.json> [batch2.json ...]

Each batch JSON is an array of objects with:
    ynab_date, outflow, current_category, amazon_order_date,
    amazon_order_total, order_items, proposed_category_group, proposed_category
"""
import csv
import json
import sys

def main():
    if len(sys.argv) < 3:
        print("Usage: merge_categorized.py <output_csv> <batch1.json> [batch2.json ...]")
        sys.exit(1)
    
    output_csv = sys.argv[1]
    batch_paths = sys.argv[2:]
    
    all_rows = []
    for path in batch_paths:
        with open(path) as f:
            batch = json.load(f)
        print(f"{path}: {len(batch)} rows")
        all_rows.extend(batch)
    
    fieldnames = [
        'entity_id', 'ynab_date', 'outflow', 'current_category',
        'amazon_order_date', 'amazon_order_total', 'order_items',
        'proposed_category_group', 'proposed_category', 'confidence'
    ]
    
    with open(output_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)
    
    print(f"\nTotal: {len(all_rows)} rows -> {output_csv}")

if __name__ == '__main__':
    main()
