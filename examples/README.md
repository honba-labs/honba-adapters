# Honba Broker Adapter Examples

Demonstrates how to discover and interact with registered broker adapters (`honba-dhan`, `honba-zerodha`)
using the canonical Honba adapter facade and role protocols (`ADR 0010`).

## Examples

- **`01_connect_dhan.py`**: Connects to Dhan or Zerodha, prints session credentials and capabilities, queries funds snapshot.
- **`04_place_order_paper.py`**: Interacts in paper mode by fetching a top-of-book quote tick, submitting a limit order, checking status, cancelling the order, and verifying account funds.
