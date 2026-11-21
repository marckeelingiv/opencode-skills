#!/usr/bin/env python3
"""Apply an entity-ID YNAB category plan through the open YNAB app."""
import argparse
import json
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mapping_json")
    parser.add_argument("--apply", action="store_true", help="Apply changes; default is a dry run")
    args = parser.parse_args()
    with open(args.mapping_json) as source:
        plan = json.load(source)
    targets = plan.get("transactions")
    if not isinstance(targets, list):
        raise SystemExit("Mapping must contain a transactions array built by build_ynab_mapping.py")

    expression = f"""
(() => {{
  const inst = window.ynab && window.ynab.YNABSharedLibWebInstance._firstInstanceCreated;
  if (!inst) throw new Error('YNAB entity manager is unavailable; open a loaded app.ynab.com tab.');
  const em = inst._entityManager, csm = inst._changeSetManager;
  const targets = {json.dumps(targets)};
  const apply = {json.dumps(args.apply)};
  const results = [];
  const counts = {{changed: 0, would_change: 0, skipped: 0, errors: 0}};
  for (const target of targets) {{
    const txn = em.getTransactionById(target.entity_id);
    if (!txn) {{ results.push(target.entity_id + '|ERROR:TRANSACTION_NOT_FOUND'); counts.errors++; continue; }}
    const payee = em.getPayeeById(txn.payeeId);
    if (!payee || !payee.name || !payee.name.toLowerCase().includes('amazon')) {{ results.push(target.entity_id + '|ERROR:NOT_AMAZON'); counts.errors++; continue; }}
    const current = em.getSubCategoryById(txn.subCategoryId);
    const currentName = current ? current.name : 'uncategorized';
    if (txn.subCategoryId === target.category_id) {{ results.push(target.entity_id + '|ALREADY_SET:' + target.category_name); counts.skipped++; continue; }}
    if (!apply) {{ results.push(target.entity_id + '|WOULD_CHANGE:' + currentName + '->' + target.category_name); counts.would_change++; continue; }}
    try {{
      csm.performAsSingleChangeSet(() => {{ txn.subCategoryId = target.category_id; }});
      results.push(target.entity_id + '|CHANGED:' + currentName + '->' + target.category_name);
      counts.changed++;
    }} catch (error) {{ results.push(target.entity_id + '|ERROR:' + error.message); counts.errors++; }}
  }}
  return JSON.stringify({{counts, results}});
}})()
"""
    script = f'''new_tab("https://app.ynab.com")
wait_for_load()
result = cdp("Runtime.evaluate", expression={json.dumps(expression)}, returnByValue=True)
value = result.get("result", {{}}).get("value")
if value is None:
    raise RuntimeError(result.get("exceptionDetails", {{}}).get("text", "Failed to update YNAB"))
print(value)
'''
    result = subprocess.run(["browser-use"], input=script, capture_output=True, text=True, timeout=120)
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    output = json.loads(result.stdout)
    print("|".join(f"{name.upper()}:{value}" for name, value in output["counts"].items()))
    print("\n".join(output["results"]))


if __name__ == "__main__":
    main()
