"""Machine-readable description of every provider and the keys it needs.

The web settings page and `avb providers` both render from this table, so
adding a vendor is one entry here plus one adapter class -- no UI work.

Secrets are never stored in avb.toml; this table only names the env var that
holds each one. The settings page writes them into `.env` (gitignored).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = [
    "EnvSpec", "ProviderSpec",
    "VIDEO_SPECS", "VOICE_SPECS", "LLM_SPECS",
    "KINDS", "spec_for", "specs_for_kind", "mask", "env_status", "describe_missing",
]


@dataclass
class EnvSpec:
    """One credential or setting the provider reads from the environment."""

    name: str
    label: str
    secret: bool = True
    required: bool = True
    hint: str = ""
    default: str = ""


@dataclass
class ProviderSpec:
    key: str                      # registry key: "seedance", "kling", ...
    label: str                    # human label shown in the UI
    kind: str                     # video | voice | llm | publish
    envs: tuple[EnvSpec, ...] = ()
    models: tuple[str, ...] = ()
    model_env: str = ""           # env var holding the model id
    console: str = ""             # where to get the key
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def ready(self) -> bool:
        return all(os.environ.get(e.name, "").strip() for e in self.envs if e.required)

    def missing(self) -> list[str]:
        return [e.name for e in self.envs if e.required and not os.environ.get(e.name, "").strip()]


KINDS = ("video", "voice", "llm")


VIDEO_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="seedance", label="Seedance (ByteDance / Volcengine Ark)", kind="video",
        envs=(EnvSpec("SEEDANCE_API_KEY", "Ark API Key",
                      hint="Volcengine Ark console → API Key management; single Bearer key"),),
        models=("doubao-seedance-2-0-pro", "doubao-seedance-2-0-mini", "doubao-seedance-1-5-pro"),
        model_env="SEEDANCE_MODEL",
        console="https://console.volcengine.com/ark",
        notes="Best Chinese-language coverage; pro is steadier, mini is cheaper.",
    ),
    ProviderSpec(
        key="kling", label="Kling (Kuaishou)", kind="video",
        envs=(EnvSpec("KLING_API_KEY", "API Key",
                      hint="Kling open platform → API key (Bearer)"),),
        models=("kling-v3", "kling-v2-5", "kling-v2-1", "kling-v1-6"),
        model_env="KLING_MODEL",
        console="https://app.klingai.com",
        notes="Big motion, strong camera language — good for hooks.",
    ),
    ProviderSpec(
        key="jimeng", label="Jimeng (Volcengine CV)", kind="video",
        envs=(
            EnvSpec("VOLC_ACCESS_KEY", "Access Key",
                    hint="Volcengine → Key management → AccessKey ID"),
            EnvSpec("VOLC_SECRET_KEY", "Secret Key",
                    hint="Pairs with the Access Key; HMAC-SHA256 signing"),
            EnvSpec("JIMENG_REQ_KEY", "req_key", required=False,
                    default="jimeng_vgfm_t2v_l20",
                    hint="Usually leave alone; set it to switch model revisions"),
        ),
        models=("jimeng_vgfm_t2v_l20", "jimeng_vgfm_t2v_l25"),
        model_env="JIMENG_REQ_KEY",
        console="https://console.volcengine.com/iam/keymanage",
        notes="HMAC two-key signing, unlike Seedance's single Bearer key. Result URLs expire in 1h, concurrency 1.",
        aliases=("dreamina",),
    ),
    ProviderSpec(
        key="veo", label="Veo (Google)", kind="video",
        envs=(EnvSpec("GOOGLE_API_KEY", "API Key",
                      hint="Google AI Studio / Vertex, Veo access required"),),
        models=("veo-3.1-generate-001", "veo-3.1-fast-generate-001", "veo-2.0-generate-001"),
        model_env="VEO_MODEL",
        console="https://aistudio.google.com/apikey",
        notes="Best audio-visual coherence, and the most expensive.",
    ),
    ProviderSpec(
        key="bailian", label="Wan (Alibaba Bailian)", kind="video",
        envs=(EnvSpec("BAILIAN_API_KEY", "DashScope API Key",
                      hint="Alibaba Bailian console → API-KEY"),),
        models=("wan2.5-t2v-preview", "wan2.2-t2v-plus", "wan2.2-t2v-flash"),
        model_env="BAILIAN_MODEL",
        console="https://bailian.console.aliyun.com",
        notes="Cheap China option, average latency. Only major family with open weights.",
        aliases=("wan",),
    ),
    ProviderSpec(
        key="minimax", label="Hailuo (MiniMax)", kind="video",
        envs=(
            EnvSpec("MINIMAX_API_KEY", "API Key",
                    hint="MiniMax platform → Account management → API Keys"),
            EnvSpec("MINIMAX_GROUP_ID", "Group ID", required=False,
                    hint="Only needed on group-billed accounts; sent as the GroupId header"),
        ),
        # The first entry is what the adapter defaults to.
        models=(
            "MiniMax-Hailuo-02", "MiniMax-Hailuo-2.3", "T2V-01-Director", "T2V-01",
            # H3 series (2026): quality-first / high-speed / lite tiers
            "MiniMax-H3", "MiniMax-H3-Highspeed", "MiniMax-H3-Lite",
        ),
        model_env="MINIMAX_MODEL",
        console="https://platform.minimax.io",
        notes="Strong on people and physics. TWO things bite mainland (中国区) accounts: (1) the key is rejected by the intl host — set MINIMAX_BASE_URL=https://api.minimaxi.com/v1; (2) mainland accounts only expose the H3 (v2) models — the Hailuo/T2V-01 v1 models return 'system error'. So pick an H3 tier here (H3 quality / H3-Highspeed balanced / H3-Lite cheap). The adapter auto-routes H3* to the v2 API and everything else to v1.",
        aliases=("hailuo",),
    ),
    ProviderSpec(
        key="zhipu", label="CogVideoX (Zhipu)", kind="video",
        envs=(EnvSpec("ZHIPU_API_KEY", "API Key",
                      hint="Zhipu open platform → API Keys"),),
        models=("cogvideox-2", "cogvideox-flash", "cogvideox"),
        model_env="ZHIPU_MODEL",
        console="https://open.bigmodel.cn",
        notes="Open weights, self-hostable; hosted tier is cheap.",
        aliases=("cogvideox",),
    ),
)


VOICE_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="edge", label="Edge TTS (free neural)", kind="voice",
        envs=(),
        # Any edge-tts short name works; these are the ones worth pinning.
        models=(
            "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural",
            "en-US-ChristopherNeural", "en-GB-SoniaNeural", "en-AU-NatashaNeural",
            "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "ja-JP-NanamiNeural",
            "es-ES-ElviraNeural",
        ),
        model_env="AVB_VOICE",
        notes="edge-tts, no key needed, word-level timings. Default choice.",
        aliases=("edge-tts",),
    ),
    ProviderSpec(
        key="elevenlabs", label="ElevenLabs (paid)", kind="voice",
        envs=(EnvSpec("ELEVENLABS_API_KEY", "API Key",
                      hint="ElevenLabs → Profile → API Key"),),
        models=("expert_female", "expert_male", "adam", "rachel", "marcus", "sofia"),
        model_env="AVB_VOICE",
        console="https://elevenlabs.io/app/settings/api-keys",
        notes="/with-timestamps endpoint, the most accurate word timings.",
    ),
    ProviderSpec(
        key="formant", label="Formant (offline)", kind="voice",
        envs=(),
        notes="Offline Klatt synthesiser. Real audio, robotic, always available.",
    ),
)


LLM_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="deepseek", label="DeepSeek (default)", kind="llm",
        envs=(EnvSpec("DEEPSEEK_API_KEY", "API Key",
                      hint="platform.deepseek.com → API Keys"),),
        models=("deepseek-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"),
        console="https://platform.deepseek.com/api_keys",
        notes="OpenAI-compatible endpoint for scene parsing and edit routing.",
    ),
    ProviderSpec(
        key="openai", label="OpenAI", kind="llm",
        envs=(EnvSpec("OPENAI_API_KEY", "API Key",
                      hint="platform.openai.com → API keys"),),
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"),
        console="https://platform.openai.com/api-keys",
        notes="Cheapest tier (gpt-4o-mini) is plenty for scene parsing.",
    ),
    ProviderSpec(
        key="moonshot", label="Moonshot / Kimi", kind="llm",
        envs=(EnvSpec("MOONSHOT_API_KEY", "API Key",
                      hint="platform.moonshot.cn → API keys"),),
        models=("moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2"),
        console="https://platform.moonshot.cn/console/api-keys",
        notes="Strong Chinese copy; OpenAI-compatible endpoint.",
    ),
    ProviderSpec(
        key="zhipu", label="Zhipu GLM", kind="llm",
        envs=(EnvSpec("ZHIPU_API_KEY", "API Key",
                      hint="Zhipu open platform → API Keys (shared with the video adapter)"),),
        models=("glm-4.6", "glm-4.5-air", "glm-4-flash"),
        console="https://open.bigmodel.cn",
        notes="Shares ZHIPU_API_KEY with the CogVideoX video adapter.",
        aliases=("glm",),
    ),
    ProviderSpec(
        key="qwen", label="Qwen (Alibaba Bailian)", kind="llm",
        envs=(EnvSpec("BAILIAN_API_KEY", "DashScope API Key",
                      hint="Bailian console → API-KEY (shared with the Wan video adapter)"),),
        models=("qwen-plus", "qwen-max", "qwen-turbo"),
        console="https://bailian.console.aliyun.com",
        notes="Shares BAILIAN_API_KEY with the Wan video adapter.",
        aliases=("bailian", "dashscope"),
    ),
    ProviderSpec(
        key="compat", label="OpenAI-compatible endpoint (custom)", kind="llm",
        envs=(EnvSpec("OPENAI_API_KEY", "API Key",
                      hint="Any OpenAI-compatible service: vLLM / Ollama / OpenRouter / a proxy"),),
        models=("gpt-4o-mini", "llama3.1-8b", "qwen2.5-7b-instruct"),
        console="https://platform.openai.com/docs/api-reference",
        notes="Set Base URL and model id by hand — this is the escape hatch for local servers and gateways.",
        aliases=("custom", "openai-compatible"),
    ),
)


_ALL = {"video": VIDEO_SPECS, "voice": VOICE_SPECS, "llm": LLM_SPECS}


def specs_for_kind(kind: str) -> tuple[ProviderSpec, ...]:
    return _ALL.get(kind, ())


def spec_for(kind: str, key: str) -> ProviderSpec | None:
    key = (key or "").strip().lower()
    for s in specs_for_kind(kind):
        if s.key == key or key in s.aliases:
            return s
    return None


def mask(value: str) -> str:
    """Show just enough of a key to recognise it, never enough to copy it."""
    v = (value or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return v[:2] + "*" * (len(v) - 2)
    return f"{v[:4]}{'*' * 6}{v[-4:]}"


def env_status(spec: ProviderSpec) -> dict:
    """Per-env-var status for the settings page."""
    return {
        e.name: {
            "label": e.label,
            "secret": e.secret,
            "required": e.required,
            "hint": e.hint,
            "default": e.default,
            "set": bool(os.environ.get(e.name, "").strip()),
            "masked": mask(os.environ.get(e.name, "")),
        }
        for e in spec.envs
    }


def describe_missing(kind: str) -> list[dict]:
    """Everything of `kind` that is not yet usable, for a first-run checklist."""
    out = []
    for s in specs_for_kind(kind):
        out.append({
            "key": s.key,
            "label": s.label,
            "ready": s.ready(),
            "missing": s.missing(),
            "console": s.console,
            "notes": s.notes,
        })
    return out
