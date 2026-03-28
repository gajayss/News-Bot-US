from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--side', required=True)
    parser.add_argument('--qty', type=int, required=True)
    parser.add_argument('--right', required=True)
    parser.add_argument('--expiry-type', required=True)
    parser.add_argument('--signal-id', required=True)
    parser.add_argument('--reason', default='')
    parser.add_argument('--reference-price', default='0')
    args = parser.parse_args()
    print(json.dumps({
        'status': 'SENT',
        'entry_mode': 'stub',
        'symbol': args.symbol,
        'side': args.side,
        'qty': args.qty,
        'right': args.right,
        'expiry_type': args.expiry_type,
        'signal_id': args.signal_id,
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
