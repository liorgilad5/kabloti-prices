"""
Daily chain prices for Kabloti (קבלותי).

Downloads the full price files that Israeli supermarket chains publish by law (חוק קידום התחרות
בענף המזון), keeps only the branches in the chosen cities, and writes one small JSON file per
city: for every product, the lowest price of each chain in that city and the branch it is at.

Standard library only. Run:  python build_prices.py --cities "באר שבע" --out public
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import ftplib
import gzip
import html
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

UA = {'User-Agent': 'Mozilla/5.0 (price-transparency reader)'}


def get(url: str, data: bytes | None = None, tries: int = 2) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))
    raise RuntimeError('unreachable')


def unpack(b: bytes) -> str:
    """Price files come as gzip, zip or plain XML, in UTF-8 or UTF-16."""
    if b[:2] == b'\x1f\x8b':
        b = gzip.decompress(b)
    elif b[:2] == b'PK':
        z = zipfile.ZipFile(io.BytesIO(b))
        b = z.read(z.namelist()[0])
    for enc in ('utf-8-sig', 'utf-16', 'cp1255'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode('utf-8', 'replace')


def parse_xml(text: str) -> ET.Element:
    text = re.sub(r'^\s*<\?xml[^>]*\?>', '', text.strip())
    return ET.fromstring(text)


def child(el: ET.Element, name: str) -> str:
    """Case-insensitive child text (chains differ: StoreId / StoreID / STOREID)."""
    low = name.lower()
    for c in el:
        if c.tag.lower() == low:
            return (c.text or '').strip()
    return ''


def stores_from_xml(text: str) -> list[dict]:
    root = parse_xml(text)
    out = []
    for el in root.iter():
        if el.tag.lower() == 'store' and len(el):
            out.append({'id': child(el, 'StoreId').lstrip('0') or '0', 'name': re.sub(r'\s+', ' ', child(el, 'StoreName')), 'city': child(el, 'City')})
    return out


def items_from_xml(text: str) -> list[tuple[str, str, int, float]]:
    """(code, name, item type, price) for every item in a PriceFull file."""
    root = parse_xml(text)
    out = []
    for el in root.iter():
        if el.tag.lower() != 'item':
            continue
        code, name, price = child(el, 'ItemCode'), re.sub(r'\s+', ' ', child(el, 'ItemName')), child(el, 'ItemPrice')
        try:
            p = float(price)
        except ValueError:
            continue
        if code and name and p > 0:
            out.append((code, name, int(child(el, 'ItemType') or '1') if child(el, 'ItemType').isdigit() else 1, p))
    return out


# ---------------------------------------------------------------- chains


class Shufersal:
    key, label = 'shufersal', 'שופרסל'
    base = 'https://prices.shufersal.co.il/FileObject/UpdateCategory?catID={cat}&storeId={store}&page=1'

    def _first_link(self, cat: int, store: str = '0') -> str:
        page = get(self.base.format(cat=cat, store=store)).decode('utf8', 'replace')
        m = re.search(r'href="(https://pricesprodpublic[^"]+)"', page)
        if not m:
            raise RuntimeError(f'shufersal: no file for cat {cat} store {store}')
        return html.unescape(m.group(1))

    def stores(self) -> list[dict]:
        return stores_from_xml(unpack(get(self._first_link(5))))

    def price_full(self, store_id: str) -> str:
        return unpack(get(self._first_link(2, store_id)))


class Carrefour:
    key, label = 'carrefour', 'קרפור'
    base = 'https://prices.carrefour.co.il/'

    def _files(self) -> tuple[str, list[str]]:
        page = get(self.base).decode('utf8', 'replace')
        folder = re.search(r"['\"](\d{8})['\"]", page).group(1)
        names = re.findall(r'"name":"([^"]+)"', page)
        return folder, names

    def stores(self) -> list[dict]:
        folder, names = self._files()
        name = next(n for n in names if n.startswith('Stores'))
        return stores_from_xml(unpack(get(f'{self.base}{folder}/{name}')))

    def price_full(self, store_id: str) -> str:
        folder, names = self._files()
        pat = re.compile(rf'^PriceFull\d+-\d+-{int(store_id):03d}-')
        name = sorted(n for n in names if pat.match(n))[-1]
        return unpack(get(f'{self.base}{folder}/{name}'))


class MahsaneiHashuk:
    key, label = 'mahsanei', 'מחסני השוק'
    edi = '7290661400001'
    api = 'https://laibcatalog.co.il/webapi/'

    def _files(self) -> list[dict]:
        if not hasattr(self, '_cache'):
            self._cache = json.loads(get(f'{self.api}api/getfiles?edi={self.edi}'))
        return self._cache

    def stores(self) -> list[dict]:
        f = sorted((x for x in self._files() if x['fileType'] in ('stores', 'storesfull')), key=lambda x: x['fileDate'])[-1]
        return stores_from_xml(unpack(get(f'{self.api}{self.edi}/{f["fileName"]}')))

    def price_full(self, store_id: str) -> str:
        fs = [x for x in self._files() if x['fileType'] == 'pricefull' and str(x['branchNumber']) == str(int(store_id))]
        f = sorted(fs, key=lambda x: x['fileDate'])[-1]
        return unpack(get(f'{self.api}{self.edi}/{f["fileName"]}'))


class Yohananof:
    """Published on the Cerberus server with the public user the chain lists on its site."""
    key, label = 'yohananof', 'יוחננוף'
    host, user = 'url.retail.publishedprices.co.il', 'yohananof'

    def _ls(self) -> list[str]:
        if not hasattr(self, '_names'):
            with ftplib.FTP(self.host, timeout=180) as f:
                f.login(self.user, '')
                self._names = f.nlst()
        return self._names

    def _read(self, name: str) -> bytes:
        buf = io.BytesIO()
        with ftplib.FTP(self.host, timeout=180) as f:
            f.login(self.user, '')
            f.retrbinary(f'RETR {name}', buf.write)
        return buf.getvalue()

    def stores(self) -> list[dict]:
        name = sorted(n for n in self._ls() if n.lower().startswith('stores'))[-1]
        return stores_from_xml(unpack(self._read(name)))

    def price_full(self, store_id: str) -> str:
        pat = re.compile(rf'^PriceFull\d+-\d+-{int(store_id):03d}-', re.I)
        name = sorted(n for n in self._ls() if pat.match(n))[-1]
        return unpack(self._read(name))


CHAINS = [Shufersal(), Carrefour(), MahsaneiHashuk(), Yohananof()]


# ---------------------------------------------------------------- cities


def city_names() -> dict[str, str]:
    """CBS settlement code -> name (some chains give the code, some the name)."""
    url = 'https://data.gov.il/api/3/action/datastore_search?resource_id=5c78e9fa-c2e2-4771-93ff-7f400a12f7ba&limit=5000'
    recs = json.loads(get(url))['result']['records']
    return {str(int(r['סמל_ישוב'])): r['שם_ישוב'].strip() for r in recs if str(r['סמל_ישוב']).strip().isdigit()}


def norm_city(s: str) -> str:
    s = s.replace('-', ' ').replace('״', '"').replace("''", '"')
    s = re.sub(r'\s+', ' ', s).strip()
    return {'ב"ש': 'באר שבע', 'תל אביב': 'תל אביב יפו', 'ת"א': 'תל אביב יפו'}.get(s, s)


def store_city(store: dict, codes: dict[str, str]) -> str:
    c = store['city'].strip()
    if c.isdigit():
        return norm_city(codes.get(str(int(c)), c))
    return norm_city(c)


# ---------------------------------------------------------------- build


def build(cities: list[str], out_dir: str, skip: set[str], fallback: list[str]) -> int:
    import os

    os.makedirs(out_dir, exist_ok=True)
    codes = city_names()
    wanted = {norm_city(c) for c in cities}
    code_of = {norm_city(v): k for k, v in codes.items()}
    chains = [c for c in CHAINS if c.key not in skip]
    per_city: dict[str, dict] = {c: {'stores': [], 'items': {}} for c in wanted}
    errors: list[str] = []
    loaded: set[str] = set()  # chains with fresh prices
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    for ci, chain in enumerate(chains):
        try:
            # wholesale branches ("סיטונאות") sell in bulk to businesses: not a shopping price
            stores = [s for s in chain.stores() if store_city(s, codes) in wanted and 'סיטונא' not in s['name']]
        except Exception as e:  # one chain down must not stop the others
            errors.append(f'{chain.key} stores: {e}')
            continue
        print(f'{chain.label}: {len(stores)} branches', file=sys.stderr)

        def load(s: dict):
            return s, items_from_xml(chain.price_full(s['id']))

        with cf.ThreadPoolExecutor(4) as pool:
            for fut in cf.as_completed([pool.submit(load, s) for s in stores]):
                try:
                    s, items = fut.result()
                except Exception as e:
                    errors.append(f'{chain.key} branch: {e}')
                    continue
                loaded.add(chain.label)
                city = per_city[store_city(s, codes)]
                city['stores'].append([ci, s['name']])
                si = len(city['stores']) - 1
                for code, name, typ, price in items:
                    # barcodes are shared by all chains; internal codes (weighed produce) only within one
                    key = code if typ == 1 and len(code) >= 7 else f'{chain.key}:{code}'
                    it = city['items'].setdefault(key, [name, {}])
                    best = it[1].get(ci)
                    if best is None or price < best[0]:
                        it[1][ci] = (price, si)

    # a chain that could not be read (some block servers outside Israel) keeps its last prices,
    # from the newest earlier file that has it (the published one, or one built in Israel)
    labels = [c.label for c in chains]
    as_of: dict[str, dict[str, str]] = {name: {l: now for l in loaded} for name in per_city}
    for name, data in per_city.items():
        code = code_of.get(name, re.sub(r'\W+', '-', name))
        for ci, label in enumerate(labels):
            if label in loaded:
                continue
            best_doc, best_at = None, ''
            for d in fallback:
                try:
                    with open(f'{d}/city-{code}.json', encoding='utf8') as f:
                        doc = json.load(f)
                except (OSError, ValueError):
                    continue
                if label not in doc.get('chains', []):
                    continue
                at = doc.get('asOf', {}).get(label, doc.get('updated', ''))
                if at > best_at:
                    best_doc, best_at = doc, at
            if not best_doc:
                continue
            old_ci = best_doc['chains'].index(label)
            store_map = {}
            for i, (sci, sname) in enumerate(best_doc['stores']):
                if sci == old_ci:
                    data['stores'].append([ci, sname])
                    store_map[i] = len(data['stores']) - 1
            for key, iname, flat in best_doc['items']:
                for i in range(0, len(flat) - 2, 3):
                    if flat[i] == old_ci and flat[i + 2] in store_map:
                        it = data['items'].setdefault(key, [iname, {}])
                        it[1][ci] = (flat[i + 1], store_map[flat[i + 2]])
            as_of[name][label] = best_at
            print(f'{name}: {label} kept from {best_at}', file=sys.stderr)

    updated = now
    index = {'v': 1, 'updated': updated, 'chains': [c.label for c in chains], 'cities': [], 'errors': errors}
    for name, data in sorted(per_city.items()):
        code = code_of.get(name, re.sub(r'\W+', '-', name))
        rows = []
        for key, (iname, best) in data['items'].items():
            flat: list = []
            for ci in sorted(best):
                flat += [ci, best[ci][0], best[ci][1]]
            rows.append([key, iname, flat])
        doc = {'v': 1, 'city': name, 'code': code, 'updated': updated, 'asOf': as_of[name], 'chains': labels, 'stores': data['stores'], 'items': rows}
        with open(f'{out_dir}/city-{code}.json', 'w', encoding='utf8') as f:
            json.dump(doc, f, ensure_ascii=False, separators=(',', ':'))
        index['cities'].append({'name': name, 'code': code, 'file': f'city-{code}.json', 'stores': len(data['stores']), 'items': len(rows)})
        print(f'{name}: {len(data["stores"])} branches, {len(rows)} products', file=sys.stderr)
    with open(f'{out_dir}/index.json', 'w', encoding='utf8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    for e in errors:
        print('ERROR', e, file=sys.stderr)
    return 0 if index['cities'] and any(c['items'] for c in index['cities']) else 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--cities', default='באר שבע', help='comma separated')
    ap.add_argument('--out', default='public')
    ap.add_argument('--skip', default='', help='chain keys to skip, comma separated')
    ap.add_argument('--fallback', default='', help='folders with earlier city files, comma separated')
    a = ap.parse_args()
    sys.exit(build([c for c in a.cities.split(',') if c.strip()], a.out, {s for s in a.skip.split(',') if s}, [d for d in a.fallback.split(',') if d]))
