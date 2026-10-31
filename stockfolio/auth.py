"""本地访问密码与会话：PBKDF2 口令哈希、进程内会话、登录限流。仅标准库。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

ALGO = "pbkdf2_sha256"
ITERATIONS = 600_000
SALT_BYTES = 16
SESSION_COOKIE = "sid"
SESSION_TTL = 30 * 24 * 3600


def hash_password(password: str, iterations: int = ITERATIONS) -> dict:
    """生成可落盘的密码记录；只保存盐和派生值，不保存明文。"""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {
        "algo": ALGO,
        "iterations": iterations,
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
    }


def verify_password(password: str, record: dict) -> bool:
    try:
        if record.get("algo") != ALGO:
            return False
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        iterations = int(record["iterations"])
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(digest, expected)


def default_auth_file() -> Path:
    return Path.home() / ".stockfolio" / "auth.json"


def load_auth(path) -> dict | None:
    """读取密码文件；文件不存在返回 None，存在但损坏则报错（避免静默变成无密码）。"""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"无法读取密码文件 {target}: {exc}") from exc
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"密码文件不是合法 JSON: {target}") from exc
    if not isinstance(record, dict):
        raise ValueError(f"密码文件格式错误: {target}")
    missing = [k for k in ("algo", "iterations", "salt", "hash") if k not in record]
    if missing:
        raise ValueError(f"密码文件缺少字段 {', '.join(missing)}: {target}")
    return record


def save_auth(path, record: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def set_password(
    auth_file=None, password: str | None = None, iterations: int = ITERATIONS
) -> Path:
    """生成/覆盖密码文件；password 为空时交互输入两次。"""
    if password is None:
        import getpass

        first = getpass.getpass("设置访问密码（至少 6 位）: ")
        second = getpass.getpass("再次输入: ")
        if first != second:
            raise ValueError("两次输入的密码不一致")
        password = first
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    return save_auth(auth_file or default_auth_file(), hash_password(password, iterations))


def clear_password(auth_file=None) -> Path:
    """关闭密码：删除密码文件（不存在也视为成功）。"""
    path = Path(auth_file) if auth_file else default_auth_file()
    path.unlink(missing_ok=True)
    return path


class Sessions:
    """进程内会话：重启即失效，浏览器只持有随机 token。"""

    def __init__(self, ttl: float = SESSION_TTL):
        self.ttl = ttl
        self._items: dict[str, float] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._items[token] = time.time() + self.ttl
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            expiry = self._items.get(token)
            if expiry is None or expiry < now:
                self._items.pop(token, None)
                return False
            self._items[token] = now + self.ttl
            return True

    def destroy(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._items.pop(token, None)


class LoginLimiter:
    """按来源 IP 限制失败次数，阻止在线爆破。"""

    def __init__(self, max_failures: int = 5, window: float = 300.0):
        self.max_failures = max_failures
        self.window = window
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, ip: str, now: float) -> None:
        queue = self._fails[ip]
        while queue and queue[0] < now - self.window:
            queue.popleft()

    def blocked(self, ip: str) -> bool:
        now = time.time()
        with self._lock:
            self._prune(ip, now)
            return len(self._fails[ip]) >= self.max_failures

    def fail(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            self._prune(ip, now)
            self._fails[ip].append(now)

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)
