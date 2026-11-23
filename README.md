# Kabloti chain prices

A daily job that reads the price files Israeli supermarket chains publish by law (חוק קידום התחרות
בענף המזון) and publishes, per city, the lowest regular price of each chain for every product.

- Chains: שופרסל, קרפור, מחסני השוק, יוחננוף
- Cities: `cities.txt` (comma separated)
- Output (GitHub Pages): `index.json`, `city-<CBS code>.json`

Only public data; nothing about users is collected or stored here.
