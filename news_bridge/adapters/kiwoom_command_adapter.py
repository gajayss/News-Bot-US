from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


def load_payload(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def build_named_args(payload: dict[str, Any], mapping: dict[str, str]) -> list[str]:
    args: list[str] = []
    for payload_key, arg_name in mapping.items():
        value = payload.get(payload_key)
        if value is None:
            continue
        args.extend([arg_name, str(value)])
    return args


def build_legacy_args(payload: dict[str, Any]) -> list[str]:
    return [
        str(payload.get('underlying', '')),
        str(payload.get('side', '')),
        str(payload.get('qty', 1)),
        str(payload.get('option_right', '')),
        str(payload.get('expiry_type', '')),
        str(payload.get('signal_id', '')),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description='Adapt generic news payload to an existing Kiwoom option entry script.')
    parser.add_argument('--payload', required=True)
    parser.add_argument('--target', required=True, help='Actual entry script or executable to invoke')
    parser.add_argument('--mode', choices=['payload-file', 'stdin-json', 'named-args', 'legacy-args'], default='named-args')
    parser.add_argument('--arg-map-file', default='')
    parser.add_argument('--success-returncodes', default='0')
    parser.add_argument('--cwd', default='')
    parser.add_argument('--python', default='python')
    args = parser.parse_args()

    payload = load_payload(args.payload)
    target = args.target
    success_codes = {int(x.strip()) for x in args.success_returncodes.split(',') if x.strip()}
    if not success_codes:
        success_codes = {0}

    is_python_script = target.lower().endswith('.py')
    cmd: list[str]
    stdin_text = None
    if is_python_script:
        cmd = [args.python, target]
    else:
        cmd = [target]

    if args.mode == 'payload-file':
        cmd.append(args.payload)
    elif args.mode == 'stdin-json':
        stdin_text = json.dumps(payload, ensure_ascii=False)
    elif args.mode == 'named-args':
        mapping = {
            'underlying': '--symbol',
            'side': '--side',
            'qty': '--qty',
            'option_right': '--right',
            'expiry_type': '--expiry-type',
            'signal_id': '--signal-id',
            'reason': '--reason',
            'reference_price': '--reference-price',
        }
        if args.arg_map_file:
            mapping = json.loads(Path(args.arg_map_file).read_text(encoding='utf-8'))
        cmd.extend(build_named_args(payload, mapping))
    else:
        cmd.extend(build_legacy_args(payload))

    env = os.environ.copy()
    env['KIWOOM_ADAPTER_PAYLOAD_FILE'] = args.payload
    env['KIWOOM_ADAPTER_PAYLOAD_JSON'] = json.dumps(payload, ensure_ascii=False)

    proc = subprocess.run(
        cmd,
        input=stdin_text,
        text=True,
        capture_output=True,
        cwd=args.cwd or None,
        env=env,
        check=False,
    )

    result = {
        'adapter': 'kiwoom_command_adapter',
        'target': target,
        'mode': args.mode,
        'cmd': cmd,
        'returncode': proc.returncode,
        'status': 'SENT' if proc.returncode in success_codes else 'FAILED',
        'stdout': (proc.stdout or '').strip(),
        'stderr': (proc.stderr or '').strip(),
        'payload': payload,
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0 if proc.returncode in success_codes else 1


if __name__ == '__main__':
    raise SystemExit(main())
