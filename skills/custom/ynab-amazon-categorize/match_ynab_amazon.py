#!/usr/bin/env python3
"""Match preflight YNAB Amazon transactions to scoped Amazon orders by amount."""
import argparse
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal


def amount(value):
    return Decimal(str(value).replace("$", "").replace(",", ""))


def order_date(value):
    return datetime.strptime(value, "%B %d, %Y").date()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("preflight_json")
    parser.add_argument("orders_json")
    parser.add_argument("output_json")
    args = parser.parse_args()
    with open(args.preflight_json) as source:
        preflight = json.load(source)
    with open(args.orders_json) as source:
        orders = json.load(source)

    by_amount = defaultdict(list)
    for order in orders:
        by_amount[amount(order["total"])].append(order)
    for candidates in by_amount.values():
        candidates.sort(key=lambda order: order_date(order["date"]))

    used, matched = set(), []
    for transaction in preflight["transactions"]:
        txn_date = datetime.strptime(transaction["date"], "%Y-%m-%d").date()
        candidates = [order for order in by_amount[amount(transaction["outflow"])] if order["orderNum"] not in used]
        candidates.sort(key=lambda order: abs((order_date(order["date"]) - txn_date).days))
        record = dict(transaction)
        if candidates:
            order = candidates[0]
            used.add(order["orderNum"])
            record.update(amazon_date=order["date"], amazon_total=order["total"], amazon_order_num=order["orderNum"], items=order["items"], match_status="matched")
        else:
            record.update(amazon_date="", amazon_total="", amazon_order_num="", items=[], match_status="no_match")
        matched.append(record)
    with open(args.output_json, "w") as output:
        json.dump(matched, output, indent=2)
    count = sum(record["match_status"] == "matched" for record in matched)
    print(f"Matched: {count}; unmatched: {len(matched) - count}")


if __name__ == "__main__":
    main()
