"""Layered configuration.

Precedence (later wins):

    built-in defaults  <  avb.toml  <  environment variables  <  explicit kwargs

The config file is plain TOML so it can live in the repo and be diffed.
Secrets never go in it -- the file only names the *env var* that holds the
key, which keeps `avb.toml` safe to commit and share.

Round-trip editing (`avb config set`) does a line-level key replacement so
comments and ordering in the file survive.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

__all__ = [
    "Config",
    "LLMConfig",
    "VoiceConfig",
    "VideoConfig",
    "RenderConfig",
    "load_config",
    "load_dotenv",
    "find_config_path",
    "env_path",
    "write_env",
    "save_config",
    "config_set",
    "DEFAULT_CONFIG_NAME",
]


DEFAULT_CONFIG_NAME = "avb.toml"


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


@dataclass
class LLMConfig:
    """Any OpenAI-compatible chat completions endpoint.

    DeepSeek, Moonshot, Zhipu, local vLLM/Ollama-with-openai-shim, and
    OpenAI itself all fit this shape.
    """

    provider: str = "deepseek"
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com/v1"
    api_key_env: str = "DEEPSEEK_API_KEY"
    max_tokens: int = 8000  # reasoning models burn budget thinking; leave headroom
    temperature: float = 0.15
    timeout: int = 120
    effort: str = "low"  # DeepSeek V4 effort knob: low | high | max
    enabled: bool = True

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()


@dataclass
class VoiceConfig:
    # auto | edge | formant | elevenlabs | azure | cosyvoice
    provider: str = "auto"
    # edge-tts voice short name, e.g. en-US-AriaNeural / zh-CN-XiaoxiaoNeural
    voice: str = "en-US-AriaNeural"
    rate: str = "+8%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    speed: float = 1.0
    energy: float = 1.0
    # formant-only knobs (offline fallback)
    formant_voice: str = "expert_female"


@dataclass
class VideoConfig:
    # auto | seedance | kling | jimeng | veo | bailian | minimax | zhipu
    # (no offline provider -- an unconfigured run fails loudly)
    provider: str = "auto"
    # provider-specific model id
    model: str = ""
    # seconds per generated clip
    clip_seconds: float = 5.0
    # hard ceiling on spend per run, USD. 0 disables the guard.
    max_usd_per_run: float = 0.0
    # poll budget for async providers
    poll_timeout_s: int = 600


@dataclass
class RenderConfig:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 18
    draft_crf: int = 24
    preset: str = "veryfast"
    workers: int = 0  # 0 = auto
    tail_pad: float = 0.22


@dataclass
class Config:
    # --- project ---
    project_name: str = "untitled"
    style: str = "dr_vsl"
    aspect: str = "9:16"
    workdir: str = ".avb"
    # --- sections ---
    llm: LLMConfig = field(default_factory=LLMConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    # --- quality control ---
    qc_max_caption_offset_ms: int = 80
    qc_min_silence_s: float = 0.35

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        parts = [
            f"llm={self.llm.provider}/{self.llm.model}" + ("" if self.llm.api_key else " (no key)"),
            f"voice={self.voice.provider}:{self.voice.voice}",
            f"video={self.video.provider}",
            f"render={self.render.width}x{self.render.height}@{self.render.fps}",
            f"style={self.style}",
        ]
        return " | ".join(parts)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


_SECTION_TYPES = {
    "llm": LLMConfig,
    "voice": VoiceConfig,
    "video": VideoConfig,
    "render": RenderConfig,
}

_ENV_MAP = {
    "AVB_LLM_PROVIDER": ("llm", "provider"),
    "AVB_LLM_MODEL": ("llm", "model"),
    "AVB_LLM_BASE_URL": ("llm", "base_url"),
    "AVB_LLM_API_KEY_ENV": ("llm", "api_key_env"),
    "AVB_LLM_ENABLED": ("llm", "enabled"),
    "AVB_VOICE_PROVIDER": ("voice", "provider"),
    "AVB_VOICE": ("voice", "voice"),
    "AVB_VOICE_RATE": ("voice", "rate"),
    "AVB_VIDEO_PROVIDER": ("video", "provider"),
    "AVB_VIDEO_MODEL": ("video", "model"),
    "AVB_VIDEO_MAX_USD": ("video", "max_usd_per_run"),
    "AVB_WORKDIR": ("workdir", None),
    "AVB_STYLE": ("style", None),
    "AVB_ASPECT": ("aspect", None),
    "AVB_RENDER_WIDTH": ("render", "width"),
    "AVB_RENDER_HEIGHT": ("render", "height"),
    "AVB_RENDER_FPS": ("render", "fps"),
    "AVB_RENDER_WORKERS": ("render", "workers"),
}


def _coerce(value: Any, target: Any) -> Any:
    """Coerce a TOML/env value to the dataclass field's type."""
    if isinstance(target, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(target, int) and not isinstance(target, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return target
    if isinstance(target, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return target
    return str(value) if value is not None else target


# --------------------------------------------------------------------------
# .env loading (no dependency on python-dotenv)
# --------------------------------------------------------------------------


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Read KEY=VALUE lines into os.environ.

    Deliberately tiny: comments, blank lines, optional `export ` prefix and
    single/double quotes. Nothing else. Existing env vars win unless
    `override=True`, which is the behaviour you want in a CLI (shell exports
    should beat the file).
    """
    candidates = []
    if path:
        candidates.append(Path(path))
    else:
        here = Path.cwd()
        candidates.extend([here / ".env", here.parent / ".env"])
        cfg = find_config_path()
        if cfg:
            candidates.insert(0, cfg.parent / ".env")

    for cand in candidates:
        if not cand.is_file():
            continue
        for line in cand.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("export "):
                s = s[7:].strip()
            if "=" not in s:
                continue
            k, _, v = s.partition("=")
            k = k.strip()
            v = v.strip().strip("'").strip('"')
            if not k:
                continue
            if override or k not in os.environ:
                os.environ[k] = v
        return cand
    return None


def env_path(create: bool = False) -> Path:
    """Where the .env lives: next to avb.toml if there is one, else cwd.

    Secrets stay out of avb.toml (which is committed), so they need a home
    of their own. One .env next to the config file keeps `avb config set`
    and the settings page writing to the same place.
    """
    cfg = find_config_path()
    base = cfg.parent if cfg else Path.cwd()
    p = base / ".env"
    if create and not p.exists():
        p.write_text(
            "# dtc-ads-video-ai-generator secrets (gitignored)\n"
            "# Written by the settings page / `avb config set-key`. Never commit this file.\n",
            encoding="utf-8",
        )
    return p


def write_env(mapping: dict[str, str], path: str | Path | None = None) -> Path:
    """Write KEY=VALUE pairs into .env, preserving comments and ordering.

    Line-level replacement rather than a full rewrite: users put notes and
    grouping comments in here, and clobbering them is rude.
    """
    p = Path(path) if path else env_path(create=True)

    existing = p.read_text(encoding="utf-8").splitlines(keepends=True) if p.is_file() else []
    if not existing:
        existing = ["# dtc-ads-video-ai-generator secrets (gitignored)\n"]

    out: list[str] = []
    pending = dict(mapping)
    for line in existing:
        s = line.strip()
        raw = s[7:].strip() if s.startswith("export ") else s
        if not raw or raw.startswith("#") or "=" not in raw:
            out.append(line)
            continue
        k = raw.partition("=")[0].strip()
        if k in pending:
            out.append(f"{k}={pending.pop(k)}\n")
        else:
            out.append(line)

    if pending:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append("\n# added by dtc-ads-video-ai-generator\n")
        for k, v in pending.items():
            out.append(f"{k}={v}\n")

    p.write_text("".join(out), encoding="utf-8")
    return p


def find_config_path(start: str | Path | None = None) -> Path | None:
    """Walk up from `start` (or cwd) looking for avb.toml."""
    here = Path(start or Path.cwd()).resolve()
    for p in [here, *here.parents]:
        cand = p / DEFAULT_CONFIG_NAME
        if cand.is_file():
            return cand
    return None


def _apply_toml(cfg: Config, data: dict[str, Any]) -> None:
    for key, value in data.items():
        if key in _SECTION_TYPES and isinstance(value, dict):
            sub = getattr(cfg, key)
            for f in fields(sub):
                if f.name in value:
                    setattr(sub, f.name, _coerce(value[f.name], getattr(sub, f.name)))
        elif hasattr(cfg, key) and not isinstance(value, dict):
            setattr(cfg, key, _coerce(value, getattr(cfg, key)))


def _apply_env(cfg: Config) -> None:
    for env_key, (section, attr) in _ENV_MAP.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        if attr is None:
            setattr(cfg, section, _coerce(raw, getattr(cfg, section)))
        else:
            sub = getattr(cfg, section)
            setattr(sub, attr, _coerce(raw, getattr(sub, attr)))


def load_config(
    path: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    use_env: bool = True,
) -> Config:
    """Build a Config from defaults + file + env + explicit overrides.

    `overrides` uses dotted keys: {"llm.model": "deepseek-v4-pro"}.
    """
    cfg = Config()

    # Secrets live in .env (gitignored); pull them in before env overrides
    # so AVB_* vars in the shell still win.
    load_dotenv()

    p = Path(path) if path else find_config_path()
    if p and p.is_file() and tomllib is not None:
        with open(p, "rb") as fh:
            _apply_toml(cfg, tomllib.load(fh))

    if use_env:
        _apply_env(cfg)

    for dotted, value in (overrides or {}).items():
        if "." in dotted:
            section, attr = dotted.split(".", 1)
            sub = getattr(cfg, section, None)
            if sub is not None and hasattr(sub, attr):
                setattr(sub, attr, _coerce(value, getattr(sub, attr)))
        elif hasattr(cfg, dotted):
            setattr(cfg, dotted, _coerce(value, getattr(cfg, dotted)))

    return cfg


# --------------------------------------------------------------------------
# Saving (comment-preserving line edit)
# --------------------------------------------------------------------------

_TOML_HEADER = """\
# dtc-ads-video-ai-generator configuration
# Docs: docs/USAGE.md
#
# Secrets are NOT stored here. This file only names the env var that holds
# each key (see `[llm].api_key_env`). Export the key in your shell or put it
# in a .env file next to this one.
"""


def _render_toml(cfg: Config) -> str:
    d = cfg.to_dict()
    lines = [_TOML_HEADER, ""]
    for section in ("llm", "voice", "video", "render"):
        lines.append(f"[{section}]")
        for k, v in d[section].items():
            lines.append(f"{k} = {_toml_value(v)}")
        lines.append("")
    lines.append("# top-level")
    for k in ("project_name", "style", "aspect", "workdir", "qc_max_caption_offset_ms", "qc_min_silence_s"):
        lines.append(f"{k} = {_toml_value(d[k])}")
    lines.append("")
    return "\n".join(lines)


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_config(cfg: Config, path: str | Path) -> Path:
    p = Path(path)
    p.write_text(_render_toml(cfg), encoding="utf-8")
    return p


def config_set(path: str | Path, dotted: str, value: str) -> tuple[bool, str]:
    """Set one key in the TOML file, preserving comments.

    Returns (changed, message).
    """
    p = Path(path)
    if not p.is_file():
        return False, f"config file not found: {p}"

    text = p.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    if "." in dotted:
        section, key = dotted.split(".", 1)
    else:
        section, key = "", dotted

    # coerce via the dataclass so `enabled=false` lands as a bool
    probe = load_config(p, use_env=False)
    if "." in dotted:
        sub = getattr(probe, section, None)
        if sub is None or not hasattr(sub, key):
            return False, f"unknown key: {dotted}"
        typed = _coerce(value, getattr(sub, key))
    else:
        if not hasattr(probe, key):
            return False, f"unknown key: {dotted}"
        typed = _coerce(value, getattr(probe, key))

    rendered = f"{key} = {_toml_value(typed)}\n"
    in_section = section == ""
    found = False
    out: list[str] = []
    current = ""

    for line in lines:
        m = re.match(r"^\s*\[([^\]]+)\]", line)
        if m:
            if in_section and not found and current == section:
                pass
            current = m.group(1).strip()
            in_section = current == section
            out.append(line)
            continue
        if in_section and re.match(rf"^\s*{re.escape(key)}\s*=", line):
            out.append(rendered)
            found = True
            continue
        out.append(line)

    if not found:
        # append a new section
        if section:
            out.append(f"\n[{section}]\n")
        out.append(rendered)

    p.write_text("".join(out), encoding="utf-8")
    return True, f"{dotted} = {_toml_value(typed)}"
