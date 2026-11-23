# Kabloti chain prices

A daily job that reads the price files Israeli supermarket chains publish by law (חוק קידום התחרות
בענף המזון) and publishes, per city, the lowest regular price of each chain for every product.

- Chains: שופרסל, קרפור, מחסני השוק, יוחננוף
- Cities: `cities.txt` (comma separated)
- Output (GitHub Pages): `index.json`, `city-<CBS code>.json`

Some chains refuse servers outside Israel. For those, the job keeps the newest prices found in
`data/` (built on a computer in Israel: `python build_prices.py --out data`) or in yesterday's
published files; each city file says in `asOf` from when every chain's prices are.

Only public data; nothing about users is collected or stored here.
