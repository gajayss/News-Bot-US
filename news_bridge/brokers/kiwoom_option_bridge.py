from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass(slots=True)
class KiwoomBridgeConfig:
    mode: str
    command: str = ""
    command_timeout_sec: int = 25
    webhook_url: str = ""
    command_working_dir: str = ""
    command_success_returncodes: str = "0"
    command_capture_stdout_json: bool = True
    command_extra_env_json: str = "{}"
    command_delete_payload_after_run: bool = False


class KiwoomOptionBridgeBroker:
    def __init__(self, cfg: KiwoomBridgeConfig) -> None:
        self.cfg = cfg
        self.session = requests.Session()

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        reason: str,
        signal_id: str,
        expiry_type: str,
        reference_price: float,
        option_right: str,
    ) -> dict[str, Any]:
        payload = {
            "underlying": symbol,
            "side": side,
            "qty": qty,
            "reason": reason,
            "signal_id": signal_id,
            "expiry_type": expiry_type,
            "reference_price": reference_price,
            "option_right": option_right,
        }

        if self.cfg.mode == "command":
            return self._call_command(payload)
        if self.cfg.mode == "webhook":
            return self._call_webhook(payload)
        raise ValueError(f"Unsupported Kiwoom bridge mode: {self.cfg.mode}")

    def _call_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.cfg.command:
            raise RuntimeError("KIWOOM_COMMAND is empty")

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
            payload_path = Path(fp.name)

        env = os.environ.copy()
        env.update(self._build_env(payload, payload_path))

        cmd_text = self._render_command(self.cfg.command, payload, payload_path)
        cmd = shlex.split(cmd_text, posix=os.name != "nt")
        success_codes = self._parse_success_codes(self.cfg.command_success_returncodes)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.cfg.command_timeout_sec,
                check=False,
                cwd=self.cfg.command_working_dir or None,
                env=env,
            )
            stdout_text = (proc.stdout or "").strip()
            stderr_text = (proc.stderr or "").strip()
            parsed_stdout = self._maybe_parse_stdout_json(stdout_text)
            status = "SENT" if proc.returncode in success_codes else "FAILED"
            return {
                "broker": "KIWOOM_BRIDGE",
                "underlying": payload["underlying"],
                "signal_id": payload["signal_id"],
                "status": status,
                "mode": "command",
                "command": cmd,
                "working_dir": self.cfg.command_working_dir or "",
                "returncode": proc.returncode,
                "success_returncodes": sorted(success_codes),
                "stdout": stdout_text,
                "stderr": stderr_text,
                "stdout_json": parsed_stdout,
                "payload_file": str(payload_path),
                "payload": payload,
            }
        finally:
            if self.cfg.command_delete_payload_after_run:
                try:
                    payload_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _call_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.cfg.webhook_url:
            raise RuntimeError("KIWOOM_WEBHOOK_URL is empty")
        resp = self.session.post(self.cfg.webhook_url, json=payload, timeout=20)
        text = resp.text
        data: dict[str, Any]
        try:
            data = resp.json()
        except Exception:
            data = {"raw_text": text}
        return {
            "broker": "KIWOOM_BRIDGE",
            "underlying": payload["underlying"],
            "signal_id": payload["signal_id"],
            "status": "SENT" if resp.ok else "FAILED",
            "mode": "webhook",
            "http_status": resp.status_code,
            "response": data,
        }

    def _render_command(self, template: str, payload: dict[str, Any], payload_path: Path) -> str:
        mapping = self._command_mapping(payload, payload_path)
        return template.format(**mapping)

    def _command_mapping(self, payload: dict[str, Any], payload_path: Path) -> dict[str, str]:
        payload_json = json.dumps(payload, ensure_ascii=False)
        payload_json_b64 = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
        return {
            "payload_file": str(payload_path),
            "payload_json": payload_json,
            "payload_json_base64": payload_json_b64,
            "underlying": str(payload.get("underlying", "")),
            "symbol": str(payload.get("underlying", "")),
            "side": str(payload.get("side", "")),
            "qty": str(payload.get("qty", "")),
            "reason": str(payload.get("reason", "")),
            "signal_id": str(payload.get("signal_id", "")),
            "expiry_type": str(payload.get("expiry_type", "")),
            "reference_price": str(payload.get("reference_price", "")),
            "option_right": str(payload.get("option_right", "")),
        }

    def _build_env(self, payload: dict[str, Any], payload_path: Path) -> dict[str, str]:
        env_map = {
            "KIWOOM_PAYLOAD_FILE": str(payload_path),
            "KIWOOM_PAYLOAD_JSON": json.dumps(payload, ensure_ascii=False),
            "KIWOOM_SIGNAL_ID": str(payload.get("signal_id", "")),
            "KIWOOM_UNDERLYING": str(payload.get("underlying", "")),
            "KIWOOM_SYMBOL": str(payload.get("underlying", "")),
            "KIWOOM_SIDE": str(payload.get("side", "")),
            "KIWOOM_QTY": str(payload.get("qty", "")),
            "KIWOOM_REASON": str(payload.get("reason", "")),
            "KIWOOM_EXPIRY_TYPE": str(payload.get("expiry_type", "")),
            "KIWOOM_REFERENCE_PRICE": str(payload.get("reference_price", "")),
            "KIWOOM_OPTION_RIGHT": str(payload.get("option_right", "")),
        }
        try:
            extra_env = json.loads(self.cfg.command_extra_env_json or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"KIWOOM_COMMAND_EXTRA_ENV_JSON invalid JSON: {exc}") from exc
        for key, value in extra_env.items():
            env_map[str(key)] = str(value)
        return env_map

    @staticmethod
    def _parse_success_codes(raw: str) -> set[int]:
        values = set()
        for part in (raw or "0").split(","):
            part = part.strip()
            if not part:
                continue
            values.add(int(part))
        return values or {0}

    def _maybe_parse_stdout_json(self, stdout_text: str) -> dict[str, Any] | None:
        if not self.cfg.command_capture_stdout_json or not stdout_text:
            return None
        try:
            parsed = json.loads(stdout_text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else {"value": parsed}
