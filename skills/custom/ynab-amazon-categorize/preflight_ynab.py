#!/usr/bin/env python3
"""Export scoped Amazon transactions and category IDs from the open YNAB budget."""
import argparse
import json
import subprocess
import sys
from datetime import date, timedelta


def browser_use(script, timeout=120):
    return subprocess.run(
        ["browser-use"], input=script, capture_output=True, text=True, timeout=timeout
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_json")
    parser.add_argument("--start-date", help="Inclusive YYYY-MM-DD date")
    parser.add_argument("--end-date", help="Inclusive YYYY-MM-DD date")
    parser.add_argument("--months-back", type=int, default=1)
    args = parser.parse_args()

    end = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start = date.fromisoformat(args.start_date) if args.start_date else end - timedelta(days=31 * args.months_back)
    if start > end:
        parser.error("--start-date must not be after --end-date")

    expression = f"""
(() => {{
  const inst = window.ynab && window.ynab.YNABSharedLibWebInstance._firstInstanceCreated;
  if (!inst) throw new Error('YNAB entity manager is unavailable; open a loaded app.ynab.com tab.');
  const em = inst._entityManager;
  const start = '{start.isoformat()}', end = '{end.isoformat()}';
  const transactions = [];
  for (const txn of em.getAllUnapprovedTransactions()) {{
    const payee = em.getPayeeById(txn.payeeId);
    const txnDate = String(txn.date);
    if (!payee || !payee.name || !payee.name.toLowerCase().includes('amazon') || txnDate < start || txnDate > end || txn.amount >= 0) continue;
    const category = em.getSubCategoryById(txn.subCategoryId);
    const group = category && em.getMasterCategoryById(category.masterCategoryId);
    transactions.push({{
      entity_id: txn.entityId,
      date: txnDate,
      payee: payee.name,
      outflow: Math.abs(txn.amount) / 1000,
      category: category ? category.name : '',
      category_group: group ? group.name : ''
    }});
  }}
  const categories = em.getAllSubCategories().map(category => {{
    const group = em.getMasterCategoryById(category.masterCategoryId);
    return {{entity_id: category.entityId, name: category.name, group: group ? group.name : ''}};
  }});
  return JSON.stringify({{scope: {{start, end}}, transactions, categories}});
}})()
"""
    script = f'''new_tab("https://app.ynab.com")
wait_for_load()
result = cdp("Runtime.evaluate", expression={json.dumps(expression)}, returnByValue=True)
value = result.get("result", {{}}).get("value")
if value is None:
    raise RuntimeError(result.get("exceptionDetails", {{}}).get("text", "Failed to read YNAB"))
print("RESULT_START")
print(value)
print("RESULT_END")
'''
    result = browser_use(script)
    try:
        start_marker = result.stdout.index("RESULT_START\n") + len("RESULT_START\n")
        end_marker = result.stdout.index("\nRESULT_END", start_marker)
        data = json.loads(result.stdout[start_marker:end_marker])
    except (ValueError, json.JSONDecodeError) as error:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"Failed to read YNAB preflight data: {error}")

    with open(args.output_json, "w") as output:
        json.dump(data, output, indent=2)
    print(f"Scoped {len(data['transactions'])} Amazon transactions from {start} through {end}")


if __name__ == "__main__":
    main()
