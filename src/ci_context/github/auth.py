"""Authentication — resolve GitHub tokens from config file or gh CLI."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


def resolve_token(cli_token: str | None = None) -> str:
    """
    Token resolution priority:
    1. cli_token (--token CLI argument)
    2. Config file (~/.config/ci-context/config.toml)
    3. gh auth token command (subprocess)

    Raises:
        AuthError: If no token found (includes tried methods in message)
    """
    tried: list[str] = []

    # 1. CLI argument takes priority
    if cli_token:
        return cli_token
    tried.append("CLI --token")

    # 2. Config file
    token = _read_config_token()
    if token:
        return token
    tried.append("config file")

    # 3. gh auth token command
    if _gh_available():
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            token = result.stdout.strip()
            if token:
                return token
        except (subprocess.SubprocessError, FileNotFoundError):
            pass  # gh command failed, continue to raise AuthError
        tried.append("gh auth token")

    # All failed
    from ci_context.github.exceptions import AuthError

    raise AuthError(tried=tuple(tried))


def _read_config_token() -> str | None:
    """Read token from config file."""
    if os.name == "nt":
        # Fallback to %USERPROFILE%\AppData\Roaming if APPDATA is unset
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        config_dir = Path(appdata) / "ci-context"
    else:
        config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ci-context"

    config_file = config_dir / "config.toml"
    if not config_file.exists():
        return None

    # Warn if config file is world-readable on Unix (security risk)
    _check_config_permissions(config_file)

    try:
        import tomllib

        with open(config_file, "rb") as f:
            config = tomllib.load(f)
        return config.get("token")
    except Exception:
        # Config parse failed, ignore
        return None


def _check_config_permissions(config_path: Path) -> None:
    """
    Warn if config file is world-readable on Unix.

    Windows uses ACLs for file security, so mode bits are not meaningful there.
    Only warns — does not refuse to read, since this is a convenience tool.
    """
    if os.name == "nt":
        return
    try:
        mode = config_path.stat().st_mode
        if mode & stat.S_IROTH:
            import warnings

            warnings.warn(
                f"Config file {config_path} is world-readable. "
                f"Run: chmod 600 {config_path}",
                stacklevel=3,
            )
    except OSError:
        pass  # stat() failure should not block the whole flow


def _gh_available() -> bool:
    """Check if gh CLI is installed."""
    return shutil.which("gh") is not None
