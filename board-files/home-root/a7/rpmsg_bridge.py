#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import termios
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_DEVICE = "/dev/ttyRPMSG0"
DEFAULT_DATA_FILE = "/home/root/PSRT_app/backend/data.json"
DEFAULT_CACHE_FILE = "/home/root/PSRT_app/backend/rpmsg_cache.jsonl"
SCHEMA_KEYS = ("heart_rates", "body_temperatures", "positions", "timestamps", "members")
LEGACY_LINE_RE = re.compile(r"^I2C:((?:[0-9A-Fa-f]{2}\s*){8})\s*\|\s*ADC:(\d+)\s*$")


def utc_timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_schema(data):
    if not isinstance(data, dict):
        data = {}
    for key in SCHEMA_KEYS:
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


def load_data(path):
    if not os.path.exists(path):
        return ensure_schema({})
    with open(path, "r", encoding="utf-8") as data_file:
        try:
            return ensure_schema(json.load(data_file))
        except json.JSONDecodeError:
            return ensure_schema({})


def save_data(path, data):
    directory = os.path.dirname(path) or "."
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as temp_file:
        temp_name = temp_file.name
        json.dump(data, temp_file, ensure_ascii=False, indent=2)
        temp_file.write("\n")
        temp_file.flush()
        os.fsync(temp_file.fileno())
    os.replace(temp_name, path)


def update_data_file(path, records):
    if not any(records.values()):
        return

    lock_path = f"{path}.lock"
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        data = load_data(path)
        for key, items in records.items():
            if not items:
                continue
            data[key].extend(items)
            data[key] = data[key][-100:]
        save_data(path, data)
        fcntl.flock(lock_file, fcntl.LOCK_UN)


def parse_json_line(line, default_member_id):
    payload = json.loads(line)
    if not isinstance(payload, dict):
        return empty_records()

    member_id = str(payload.get("member_id", default_member_id))
    timestamp = str(payload.get("timestamp") or utc_timestamp())
    records = empty_records()
    timing_fields = {}
    if payload.get("seq") is not None:
        timing_fields["seq"] = payload.get("seq")
    if payload.get("tick_ms") is not None:
        timing_fields["tick_ms"] = payload.get("tick_ms")

    heart_rate = payload.get("heart_rate")
    if heart_rate is None and payload.get("adc") is not None:
        heart_rate = payload.get("adc")

    if heart_rate is not None:
        records["heart_rates"].append({
            "member_id": member_id,
            "heart_rate": heart_rate,
            "timestamp": timestamp,
            **timing_fields,
        })

    if payload.get("temperature") is not None:
        records["body_temperatures"].append({
            "member_id": member_id,
            "temperature": payload.get("temperature"),
            "timestamp": timestamp,
            **timing_fields,
        })

    if payload.get("latitude") is not None and payload.get("longitude") is not None:
        records["positions"].append({
            "member_id": member_id,
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "timestamp": timestamp,
            **timing_fields,
        })

    event = {
        "member_id": member_id,
        "timestamp": timestamp,
        "source": "rpmsg_json",
        "raw": payload,
    }
    event.update(timing_fields)
    if payload.get("adc") is not None:
        event["adc"] = payload.get("adc")
    if payload.get("i2c") is not None:
        event["i2c_bytes"] = payload.get("i2c")
    records["timestamps"].append(event)
    return records


def parse_legacy_line(line, member_id):
    match = LEGACY_LINE_RE.match(line)
    if not match:
        return empty_records()

    timestamp = utc_timestamp()
    i2c_bytes = [int(value, 16) for value in match.group(1).split()]
    adc_value = int(match.group(2))
    records = empty_records()
    records["heart_rates"].append({
        "member_id": member_id,
        "heart_rate": adc_value,
        "timestamp": timestamp,
    })
    records["timestamps"].append({
        "member_id": member_id,
        "timestamp": timestamp,
        "source": "rpmsg_legacy",
        "raw": line,
        "i2c_bytes": i2c_bytes,
        "adc": adc_value,
    })
    return records


def parse_line(line, default_member_id):
    line = line.strip()
    if not line:
        return empty_records()
    if line.startswith("{"):
        return parse_json_line(line, default_member_id)
    return parse_legacy_line(line, default_member_id)


def empty_records():
    return {key: [] for key in SCHEMA_KEYS}


# 持久化 JSONL FIFO 缓存：追加新记录，超限时丢弃最旧记录。
def jsonl_dumps(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


def atomic_write_lines(path, lines):
    directory = os.path.dirname(path) or "."
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as temp_file:
        temp_name = temp_file.name
        temp_file.writelines(lines)
        temp_file.flush()
        os.fsync(temp_file.fileno())
    os.replace(temp_name, path)


def enforce_cache_limits_locked(path, max_lines, max_bytes):
    if not os.path.exists(path):
        return 0, 0

    with open(path, "r", encoding="utf-8") as cache_file:
        lines = cache_file.readlines()

    original_count = len(lines)
    if max_lines > 0 and len(lines) > max_lines:
        # 保留最新记录，相当于环形缓冲区覆盖头部的旧数据。
        lines = lines[-max_lines:]

    if max_bytes > 0:
        total_bytes = sum(len(line.encode("utf-8")) for line in lines)
        while lines and total_bytes > max_bytes:
            # 字节数超限时，也从 FIFO 缓存最旧的一端开始删除。
            total_bytes -= len(lines[0].encode("utf-8"))
            lines = lines[1:]

    dropped = original_count - len(lines)
    if dropped:
        atomic_write_lines(path, lines)
    return dropped, len(lines)


def append_cache_entry(path, entry, max_lines, max_bytes):
    if not path:
        return 0, 0

    directory = os.path.dirname(path) or "."
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

    lock_path = f"{path}.lock"
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        with open(path, "a", encoding="utf-8") as cache_file:
            cache_file.write(jsonl_dumps(entry))
            cache_file.flush()
            os.fsync(cache_file.fileno())
        # 先追加再在同一把锁内裁剪，保证缓存保持环形缓冲区语义。
        dropped, kept = enforce_cache_limits_locked(path, max_lines, max_bytes)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    return dropped, kept


def cache_stats(path):
    if not path or not os.path.exists(path):
        return 0, 0
    with open(path, "r", encoding="utf-8") as cache_file:
        lines = sum(1 for _ in cache_file)
    return lines, os.path.getsize(path)


def raw_payload_from_line(line):
    if line.startswith("{"):
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return line


def build_cache_entry(line, records, default_member_id):
    stored_at = utc_timestamp()
    raw_payload = raw_payload_from_line(line)
    member_id = default_member_id
    seq = None
    tick_ms = None

    if isinstance(raw_payload, dict):
        member_id = str(raw_payload.get("member_id", default_member_id))
        seq = raw_payload.get("seq")
        tick_ms = raw_payload.get("tick_ms")

    cache_id = f"{member_id}-{seq}" if seq is not None else f"{member_id}-{stored_at}-{time.monotonic_ns()}"
    entry = {
        "cache_id": cache_id,
        "stored_at": stored_at,
        "member_id": member_id,
        "uploaded": False,
        "attempts": 0,
        "last_error": None,
        "raw": raw_payload,
        "records": records,
    }
    if seq is not None:
        entry["seq"] = seq
    if tick_ms is not None:
        entry["tick_ms"] = tick_ms
    return entry


def read_cache_batch(path, limit):
    if not path or not os.path.exists(path) or limit <= 0:
        return []

    lock_path = f"{path}.lock"
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        entries = []
        with open(path, "r", encoding="utf-8") as cache_file:
            # 上传时先取最旧记录，保持 RPMsg 接收顺序。
            for line in cache_file:
                if len(entries) >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                entries.append(json.loads(line))
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    return entries


def remove_cache_prefix(path, count):
    if not path or not os.path.exists(path) or count <= 0:
        return 0

    lock_path = f"{path}.lock"
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        with open(path, "r", encoding="utf-8") as cache_file:
            lines = cache_file.readlines()
        # 上传成功后删除已发送记录，推进 FIFO 头部。
        removed = min(count, len(lines))
        atomic_write_lines(path, lines[removed:])
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    return removed


def mark_cache_batch_error(path, count, error_message):
    if not path or not os.path.exists(path) or count <= 0:
        return

    lock_path = f"{path}.lock"
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        with open(path, "r", encoding="utf-8") as cache_file:
            lines = cache_file.readlines()

        updated = []
        for index, line in enumerate(lines):
            if index >= count:
                updated.append(line)
                continue
            try:
                entry = json.loads(line)
                entry["attempts"] = int(entry.get("attempts") or 0) + 1
                entry["last_error"] = error_message[:200]
                updated.append(jsonl_dumps(entry))
            except (json.JSONDecodeError, TypeError, ValueError):
                updated.append(line)

        atomic_write_lines(path, updated)
        fcntl.flock(lock_file, fcntl.LOCK_UN)


def upload_entries(url, entries, timeout):
    payload = json.dumps({"items": entries}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.getcode()
    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status}")


def flush_cache(path, upload_url, batch_size, timeout):
    if not upload_url:
        return 0, None

    entries = read_cache_batch(path, batch_size)
    if not entries:
        return 0, None

    try:
        upload_entries(upload_url, entries, timeout)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        mark_cache_batch_error(path, len(entries), str(exc))
        return 0, exc

    removed = remove_cache_prefix(path, len(entries))
    return removed, None


def wait_for_device(path, timeout_seconds):
    deadline = time.time() + timeout_seconds
    while True:
        if os.path.exists(path):
            return
        if time.time() >= deadline:
            raise FileNotFoundError(path)
        time.sleep(1)


def send_command(device_fd, command):
    os.write(device_fd, f"{command.strip()}\n".encode("utf-8"))


def read_lines(device_fd):
    pending = b""
    while True:
        chunk = os.read(device_fd, 256)
        if not chunk:
            time.sleep(0.1)
            continue
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            yield line.decode("utf-8", errors="replace").strip()


def flush_input(device_fd):
    try:
        termios.tcflush(device_fd, termios.TCIFLUSH)
    except OSError:
        pass


def log_cache_status(path, upload_url):
    lines, size = cache_stats(path)
    upload_state = "enabled" if upload_url else "disabled"
    print(f"cache status: path={path} lines={lines} bytes={size} upload={upload_state}", flush=True)


def build_parser():
    parser = argparse.ArgumentParser(description="Bridge STM32MP157 M4 RPMsg data into PSRT_app data.json")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--data", default=DEFAULT_DATA_FILE)
    parser.add_argument("--cache", default=DEFAULT_CACHE_FILE)
    parser.add_argument("--cache-max-lines", type=int, default=10000)
    parser.add_argument("--cache-max-bytes", type=int, default=5242880)
    parser.add_argument("--cache-status-interval", type=int, default=30)
    parser.add_argument("--upload-url", default="")
    parser.add_argument("--upload-batch-size", type=int, default=50)
    parser.add_argument("--upload-timeout", type=int, default=5)
    parser.add_argument("--member-id", default="1")
    parser.add_argument("--period", type=int, default=1000)
    parser.add_argument("--wait-device-seconds", type=int, default=30)
    parser.add_argument("--drop-buffer", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()
    wait_for_device(args.device, args.wait_device_seconds)
    last_status_at = 0

    device_fd = os.open(args.device, os.O_RDWR | os.O_NOCTTY)
    try:
        if args.drop_buffer:
            flush_input(device_fd)
        if args.period > 0:
            send_command(device_fd, f"PERIOD={args.period}")
        send_command(device_fd, "EN=1")
        print(f"rpmsg bridge reading {args.device} into {args.data}", flush=True)

        for line in read_lines(device_fd):
            if not line:
                continue
            try:
                records = parse_line(line, args.member_id)
                if any(records.values()):
                    entry = build_cache_entry(line, records, args.member_id)
                    # 先落盘再上传，离线期间的数据之后还能补传。
                    dropped, kept = append_cache_entry(args.cache, entry, args.cache_max_lines, args.cache_max_bytes)
                    if dropped:
                        print(f"cache overflow: dropped={dropped} kept={kept}", flush=True)
                    update_data_file(args.data, records)
                    sent, error = flush_cache(args.cache, args.upload_url, args.upload_batch_size, args.upload_timeout)
                    if sent:
                        lines, _ = cache_stats(args.cache)
                        print(f"cache flush: sent={sent} remaining={lines}", flush=True)
                    elif error:
                        print(f"cache flush failed: {error}", file=sys.stderr, flush=True)
                    print(f"stored: {line}", flush=True)
                else:
                    print(f"ignored: {line}", flush=True)

                now = time.time()
                if args.cache_status_interval > 0 and now - last_status_at >= args.cache_status_interval:
                    log_cache_status(args.cache, args.upload_url)
                    last_status_at = now
            except Exception as exc:
                print(f"error processing line {line!r}: {exc}", file=sys.stderr, flush=True)
    finally:
        os.close(device_fd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
