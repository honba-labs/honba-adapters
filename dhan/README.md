# honba-dhan

## Running the live tests

```fish
set -x DHAN_CLIENT_ID <id>
set -x DHAN_ACCESS_TOKEN <token>
cd ~/Devel/Finance/honba-labs/honba-adapters
set -x PYTHONPATH $PWD/dhan/src:$PWD/shared/src
python -m pytest -m live dhan/tests/live -v
```

They connect, check funds, and read positions, holdings, orders and trades. They skip on their own if the two environment variables aren't set.

