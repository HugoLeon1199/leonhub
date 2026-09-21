"""Read only an already-running, isolated MT5 research terminal.

No login, terminal launch, account/position reads, trading or scheduling. The
terminal must already have automated trading disabled. A snapshot expires in
90 seconds on the website; this tool alone is not a continuous HTTPS feed.
Use the existing MT5 Python environment; the browser/Deno owns all formulas.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time


def collect(terminal: Path, symbol: str) -> dict:
    import MetaTrader5 as mt5

    terminal = terminal.resolve(strict=True)
    # initialize() can launch a terminal. Refuse before calling it unless this
    # exact executable is running, so a typo never starts another account.
    command = "Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path | ConvertTo-Json -Compress"
    text = subprocess.check_output(['powershell', '-NoProfile', '-Command', command], text=True).strip()
    paths = json.loads(text) if text else []
    paths = [paths] if isinstance(paths, str) else paths
    if str(terminal).lower() not in {str(p).lower() for p in paths}:
        raise RuntimeError('The specified research terminal is not running; no terminal was started')
    try:
        if not mt5.initialize(str(terminal), portable=True, timeout=15000):
            raise RuntimeError(f'MT5 IPC unavailable: {mt5.last_error()}')
        info = mt5.terminal_info()
        if not info or not info.connected or info.trade_allowed:
            raise RuntimeError('Require a connected research terminal with automated trading disabled')
        if Path(info.data_path).resolve() != terminal.parent:
            raise RuntimeError('Refusing a different terminal data directory')
        instrument = mt5.symbol_info(symbol)
        if not instrument or instrument.currency_base != 'XAU' or instrument.currency_profit != 'USD':
            raise RuntimeError('Require a broker XAU/USD symbol; no alternative gold instrument')
        tick = mt5.symbol_info_tick(symbol)
        if not tick or tick.bid <= 0 or tick.ask < tick.bid:
            raise RuntimeError('Missing usable bid/ask quote')
        rows = {}
        for key, tf in [('h1', mt5.TIMEFRAME_H1), ('h4', mt5.TIMEFRAME_H4)]:
            rates = mt5.copy_rates_from_pos(symbol, tf, 1, 500)
            if rates is None or len(rates) < 280:
                raise RuntimeError(f'Missing closed {key} history')
            rows[key] = [{k: int(r[k]) if k == 'time' else float(r[k])
                          for k in ('time', 'open', 'high', 'low', 'close')} for r in rates]
        return dict(asset='XAU', now=int(time.time()*1000), source=dict(
            name=f'MT5 broker · {symbol} · bid candles', kind='broker-xauusd'),
            quote=dict(price=float(tick.bid), ask=float(tick.ask), at=int(tick.time_msc)), **rows)
    finally:
        mt5.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--terminal', required=True, type=Path)
    parser.add_argument('--symbol', default='XAUUSD')
    parser.add_argument('--output', type=Path, default=Path('.tmp_xau_snapshot.json'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    deno = shutil.which('deno')
    if not deno:
        raise RuntimeError('Deno is required to reuse the browser formulas')
    raw = collect(args.terminal, args.symbol)
    proc = subprocess.run([deno, 'run', str(Path(__file__).with_name('mt5_chart_features.js'))],
                          input=json.dumps(raw), text=True, encoding='utf-8', capture_output=True, check=True)
    snapshot = json.loads(proc.stdout)
    if not args.dry_run:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print(json.dumps({'asset': 'XAU', 'version': snapshot['version'],
                      'updated_at': snapshot['updated_at'], 'quote_at': snapshot['quote']['at'],
                      'h1_bars': snapshot['h1']['count'], 'h4_bars': snapshot['h4']['count'],
                      'written': None if args.dry_run else str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
