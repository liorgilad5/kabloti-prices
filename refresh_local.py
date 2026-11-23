"""
Refreshes data/ (prices of the chains that block servers abroad) from this computer in Israel and
pushes it, so the daily job publishes them. Runs from a Claude Code hook on every prompt; does
nothing if data/ was refreshed in the last 12 hours or another refresh is running. Never raises:
errors go to refresh.log.
"""
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data', 'index.json')
LOG = os.path.join(HERE, 'refresh.log')
LOCK = os.path.join(HERE, '.refresh.lock')
MAX_AGE = 12 * 60 * 60


def log(msg: str) -> None:
    with open(LOG, 'a', encoding='utf8') as f:
        f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {msg}\n')


def run(*args: str) -> str:
    r = subprocess.run(args, cwd=HERE, capture_output=True, text=True, encoding='utf8', timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f'{" ".join(args[:3])}: {r.stderr.strip()[-500:]}')
    return r.stdout


def main() -> None:
    if os.path.exists(DATA) and time.time() - os.path.getmtime(DATA) < MAX_AGE:
        return
    # a lock older than 20 minutes is left from a run that died
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 20 * 60:
        return
    open(LOCK, 'w').close()
    try:
        with open(os.path.join(HERE, 'cities.txt'), encoding='utf8') as f:
            cities = f.read().strip()
        # Yohananof is left out on purpose (its server is not to be accessed from here)
        run(sys.executable, '-I', 'build_prices.py', '--cities', cities, '--out', 'data', '--skip', 'yohananof')
        run('git', 'pull', '--rebase', '--quiet')
        run('git', 'add', 'data')
        if run('git', 'status', '--porcelain', 'data').strip():
            run('git', 'commit', '--quiet', '-m', 'Prices refreshed in Israel')
            run('git', 'push', '--quiet')
            log('refreshed and pushed')
        else:
            log('no changes')
    except Exception:
        log('ERROR ' + traceback.format_exc().strip().replace('\n', ' | ')[-1500:])
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == '__main__':
    main()
