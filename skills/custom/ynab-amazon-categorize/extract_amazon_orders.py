#!/usr/bin/env python3
"""Extract only the Amazon order pages needed for a YNAB transaction scope."""
import argparse
import json
import subprocess
import sys
from datetime import date, datetime

EXTRACTION_JS = r"""(() => {
  const results = [];
  for (const card of document.querySelectorAll('.order-card')) {
    const lines = card.innerText.split('\n').map(line => line.trim()).filter(Boolean);
    let date = '', total = '', orderNum = '';
    for (let i = 0; i < lines.length; i++) {
      if (lines[i] === 'ORDER PLACED' && lines[i + 1]) date = lines[i + 1];
      if (lines[i] === 'TOTAL' && lines[i + 1]) total = lines[i + 1];
      if (lines[i].startsWith('ORDER #')) orderNum = lines[i].replace('ORDER # ', '');
    }
    const items = [...card.querySelectorAll('a.a-link-normal')]
      .map(link => link.textContent.trim())
      .filter(text => text && !/^(View|Buy|Track|Write|Ask|Return|Archive|Cancel|Leave)/.test(text) && text.length > 5 && text.length < 400);
    results.push({date, total, orderNum, items});
  }
  return JSON.stringify(results);
})()"""


def parse_order_date(value):
    try:
        return datetime.strptime(value, "%B %d, %Y").date()
    except ValueError:
        return None


def build_script(year, pages, start_date):
    lines = ["import json, time", "all_orders = []", "seen = set()"]
    for page in range(pages):
        url = f"https://www.amazon.com/your-orders/orders?timeFilter=year-{year}&startIndex={page * 10}"
        lines.extend([
            f"goto_url({json.dumps(url)})", "wait_for_load()", "time.sleep(2)",
            f"result = cdp('Runtime.evaluate', expression={json.dumps(EXTRACTION_JS)}, returnByValue=True)",
            "orders = json.loads(result.get('result', {}).get('value', '[]'))",
            "if not orders: break",
            "for order in orders:",
            "    key = (order.get('orderNum', ''), order.get('total', ''))",
            "    if key not in seen:", "        seen.add(key)", "        all_orders.append(order)",
            f"if any(order.get('date') == {start_date.isoformat()!r} or (order.get('date') and __import__('datetime').datetime.strptime(order['date'], '%B %d, %Y').date() <= __import__('datetime').date.fromisoformat({start_date.isoformat()!r})) for order in orders): break",
        ])
    lines.extend(["print('RESULT_START')", "print(json.dumps(all_orders))", "print('RESULT_END')"])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_json")
    parser.add_argument("--preflight", required=True, help="Output of preflight_ynab.py")
    parser.add_argument("--pages", type=int, default=15)
    args = parser.parse_args()
    with open(args.preflight) as source:
        preflight = json.load(source)
    start = date.fromisoformat(preflight["scope"]["start"])
    end = date.fromisoformat(preflight["scope"]["end"])
    script = build_script(start.year, args.pages, start)
    result = subprocess.run(["browser-use"], input=script, capture_output=True, text=True, timeout=600)
    try:
        begin = result.stdout.index("RESULT_START\n") + len("RESULT_START\n")
        finish = result.stdout.index("\nRESULT_END", begin)
        orders = [order for order in json.loads(result.stdout[begin:finish]) if (order_date := parse_order_date(order.get("date", ""))) and start <= order_date <= end]
    except (ValueError, json.JSONDecodeError) as error:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"Failed to extract Amazon orders: {error}")
    with open(args.output_json, "w") as output:
        json.dump(orders, output, indent=2)
    print(f"Saved {len(orders)} Amazon orders from {start} through {end}")


if __name__ == "__main__":
    main()
