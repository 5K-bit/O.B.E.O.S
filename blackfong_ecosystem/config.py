from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    """
    Centralized filesystem layout for Blackcomputer.
    Adjust these if you relocate any directory.
    """

    root: Path
    data_root: Path
    artifacts_root: Path
    capsules_root: Path
    events_db: Path
    vector_db_path: Path
    text_inbox_path: Path
    knowledge_root: Path
    knowledge_cleaned_path: Path
    knowledge_chunks_path: Path
    knowledge_intel_path: Path
    source_registry_path: Path
    collection_registry_path: Path
    knowledge_bundle_root: Path

    @staticmethod
    def from_root(root: Path) -> "Paths":
        data_root = root / "data"
        artifacts_root = data_root / "artifacts"
        capsules_root = artifacts_root / "capsules"
        events_db = data_root / "events.db"
        vector_db_path = data_root / "chroma"
        text_inbox_path = artifacts_root / "text_inbox"
        knowledge_root = data_root / "knowledge"
        knowledge_cleaned_path = knowledge_root / "cleaned"
        knowledge_chunks_path = knowledge_root / "chunks"
        knowledge_intel_path = knowledge_root / "intel"
        source_registry_path = knowledge_root / "source_registry.json"
        collection_registry_path = knowledge_root / "collections.json"
        knowledge_bundle_root = Path(
            os.getenv("BLACKFONG_KNOWLEDGE_BUNDLE_PATH", str(root / "knowledge_seed"))
        )
        return Paths(
            root=root,
            data_root=data_root,
            artifacts_root=artifacts_root,
            capsules_root=capsules_root,
            events_db=events_db,
            vector_db_path=vector_db_path,
            text_inbox_path=text_inbox_path,
            knowledge_root=knowledge_root,
            knowledge_cleaned_path=knowledge_cleaned_path,
            knowledge_chunks_path=knowledge_chunks_path,
            knowledge_intel_path=knowledge_intel_path,
            source_registry_path=source_registry_path,
            collection_registry_path=collection_registry_path,
            knowledge_bundle_root=knowledge_bundle_root,
        )


@dataclass(frozen=True)
class KnowledgeSettings:
    """Runtime settings for the knowledge intelligence layer.

    ``compute_mode`` controls how knowledge features (gaps, summaries, librarian,
    influence) are allowed to produce natural-language output:
    ``deterministic`` (never call a model), ``local_llm`` (require the local model),
    or ``hybrid`` (use the local model when available, otherwise deterministic
    fallback). Deterministic services remain the source of truth in every mode.
    """

    compute_mode: str = "hybrid"

    @staticmethod
    def from_env() -> "KnowledgeSettings":
        return KnowledgeSettings(
            compute_mode=os.getenv("BLACKFONG_KNOWLEDGE_COMPUTE_MODE", "hybrid").strip().lower() or "hybrid",
        )


@dataclass(frozen=True)
class SystemIdentity:
    """
    Immutable identity and philosophy flags for the ecosystem.
    """

    system_name: str = "BLACKFONG_ECOSYSTEM"
    offline_first: bool = True
    singular_daise: bool = True  # 1/1, not forked


@dataclass(frozen=True)
class LlamaCppSettings:
    """Runtime settings for the dedicated D.A.I.S.E. llama.cpp server.

    llama-server loads one model per process, so this is scoped to a single
    model (the edited Gemma model D.A.I.S.E. speaks through) rather than the
    many-model chains Ollama still serves for the other agents.
    """

    enabled: bool = True
    binary_path: str = ""
    model_path: str = ""
    mmproj_path: str = ""
    host: str = "127.0.0.1"
    port: int = 8091
    model_alias: str = "gemma4-heretic"
    context_size: int = 8192
    threads: int = 4
    reasoning_budget: int = 0
    startup_timeout: float = 120.0
    request_timeout: float = 180.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def is_configured(self) -> bool:
        return bool(self.binary_path and self.model_path and Path(self.binary_path).is_file() and Path(self.model_path).is_file())

    @staticmethod
    def from_env(root: Path) -> "LlamaCppSettings":
        runtime_root = root / "runtime" / "llamacpp"
        default_binary = runtime_root / "build_b11026" / "llama-server.exe"
        default_model = runtime_root / "models" / "gemma4-heretic-E4B-Q4_K_M.gguf"
        default_mmproj = runtime_root / "models" / "gemma4-heretic-E4B-mmproj.gguf"
        return LlamaCppSettings(
            enabled=os.getenv("BLACKFONG_LLAMACPP_ENABLED", "true").lower() != "false",
            binary_path=os.getenv("BLACKFONG_LLAMACPP_BINARY", str(default_binary)),
            model_path=os.getenv("BLACKFONG_LLAMACPP_MODEL_PATH", str(default_model)),
            mmproj_path=os.getenv("BLACKFONG_LLAMACPP_MMPROJ_PATH", str(default_mmproj)),
            host=os.getenv("BLACKFONG_LLAMACPP_HOST", "127.0.0.1"),
            port=int(os.getenv("BLACKFONG_LLAMACPP_PORT", "8091")),
            model_alias=os.getenv("BLACKFONG_LLAMACPP_MODEL_ALIAS", "gemma4-heretic"),
            context_size=int(os.getenv("BLACKFONG_LLAMACPP_CONTEXT_SIZE", "8192")),
            threads=int(os.getenv("BLACKFONG_LLAMACPP_THREADS", "4")),
            reasoning_budget=int(os.getenv("BLACKFONG_LLAMACPP_REASONING_BUDGET", "0")),
            startup_timeout=float(os.getenv("BLACKFONG_LLAMACPP_STARTUP_TIMEOUT", "120")),
            request_timeout=float(os.getenv("BLACKFONG_LLAMACPP_REQUEST_TIMEOUT", "180")),
        )


@dataclass(frozen=True)
class ChatSettings:
    """Runtime settings for the local chat and memory stack."""

    backend: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    daise_model: str = "gemma4-heretic:latest"
    daise_backend: str = "llamacpp"
    daise_num_predict: int = 2048
    daise_num_ctx: int = 8192
    # Earth's scene explanation has its own profile: a large evidence prompt on
    # whatever CPU the operator has, so it favours a small fast model over depth.
    earth_explanation_model: str = "gemma3:1b-it-qat"
    earth_explanation_timeout: float = 120.0
    earth_explanation_num_predict: int = 300
    earth_evidence_chars: int = 6000
    ollama_timeout: float = 180.0
    ollama_keep_alive: str = "30m"
    ollama_num_ctx: int = 8192
    ollama_num_predict: int = 384
    # Response-mode model routing
    fast_model: str = "llama3.2:3b"
    fast_model_fallbacks: tuple[str, ...] = ("llama3.2:3b",)
    standard_model: str = "llama3.2:3b"
    standard_model_fallbacks: tuple[str, ...] = ("llama3.2:1b",)
    deep_model: str = "deepseek-r1:8b"
    deep_model_fallbacks: tuple[str, ...] = ("llama3.2:3b",)
    fast_timeout: float = 60.0
    fast_keep_alive: str = "30m"
    fast_num_ctx: int = 4096
    fast_num_predict: int = 384
    standard_timeout: float = 90.0
    standard_keep_alive: str = "30m"
    standard_num_ctx: int = 6144
    standard_num_predict: int = 256
    deep_timeout: float = 180.0
    deep_keep_alive: str = "20m"
    deep_num_ctx: int = 8192
    deep_num_predict: int = 512
    fast_prompt_budget_tokens: int = 600
    standard_prompt_budget_tokens: int = 1800
    deep_prompt_budget_tokens: int = 4096
    fast_global_memory_limit: int = 2
    standard_global_memory_limit: int = 4
    jarvis_model: str = "llama3.2:3b"
    jarvis_timeout: float = 180.0
    jarvis_keep_alive: str = "30m"
    jarvis_num_ctx: int = 8192
    jarvis_num_predict: int = 256
    jarvis_quick_timeout: float = 60.0
    jarvis_quick_num_predict: int = 384
    quick_prompt_budget_tokens: int = 600
    coding_model: str = "deepseek-coder:6.7b"
    coding_model_fallbacks: tuple[str, ...] = ("deepseek-coder", "llama3.2:3b")
    coding_timeout: float = 180.0
    coding_keep_alive: str = "30m"
    coding_num_ctx: int = 8192
    coding_num_predict: int = 512
    lilo_model: str = "llama3.2:3b"
    lilo_timeout: float = 120.0
    lilo_keep_alive: str = "20m"
    lilo_num_ctx: int = 8192
    lilo_num_predict: int = 320
    stitch_model: str = "llama3.2:3b"
    stitch_timeout: float = 120.0
    stitch_keep_alive: str = "20m"
    stitch_num_ctx: int = 8192
    stitch_num_predict: int = 320
    voice_model: str = "llama3.2:3b"
    voice_timeout: float = 120.0
    voice_keep_alive: str = "20m"
    voice_num_ctx: int = 4096
    voice_num_predict: int = 192
    transcript_window: int = 16
    global_memory_limit: int = 6
    summary_cadence: int = 6

    def _parse_model_chain(self, primary: str, fallbacks: str | tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(fallbacks, str):
            extra = tuple(item.strip() for item in fallbacks.split(",") if item.strip())
        else:
            extra = tuple(fallbacks)
        chain: list[str] = []
        for candidate in (primary, *extra):
            if candidate and candidate not in chain:
                chain.append(candidate)
        return tuple(chain)

    def fast_model_chain(self) -> tuple[str, ...]:
        return self._parse_model_chain(self.fast_model, self.fast_model_fallbacks)

    def standard_model_chain(self) -> tuple[str, ...]:
        return self._parse_model_chain(self.standard_model, self.standard_model_fallbacks)

    def deep_model_chain(self) -> tuple[str, ...]:
        return self._parse_model_chain(self.deep_model, self.deep_model_fallbacks)

    def coding_model_chain(self) -> tuple[str, ...]:
        return self._parse_model_chain(self.coding_model, self.coding_model_fallbacks)

    def voice_model_chain(self) -> tuple[str, ...]:
        return self.fast_model_chain()

    def model_for_agent(self, agent_name: str | None = None) -> str:
        normalized = (agent_name or "").strip().lower()
        per_agent_models = {
            "daise": self.daise_model,
            "jarvis": self.daise_model,
            "coding": self.coding_model,
            "lilo": self.lilo_model,
            "stitch": self.stitch_model,
            "voice": self.voice_model,
        }
        return per_agent_models.get(normalized) or self.ollama_model

    def timeout_for_agent(self, agent_name: str | None = None) -> float:
        normalized = (agent_name or "").strip().lower()
        per_agent_timeouts = {
            "daise": self.jarvis_timeout,
            "jarvis": self.jarvis_timeout,
            "coding": self.coding_timeout,
            "lilo": self.lilo_timeout,
            "stitch": self.stitch_timeout,
            "voice": self.voice_timeout,
        }
        return per_agent_timeouts.get(normalized) or self.ollama_timeout

    def keep_alive_for_agent(self, agent_name: str | None = None) -> str:
        normalized = (agent_name or "").strip().lower()
        per_agent_keep_alive = {
            "daise": self.jarvis_keep_alive,
            "jarvis": self.jarvis_keep_alive,
            "coding": self.coding_keep_alive,
            "lilo": self.lilo_keep_alive,
            "stitch": self.stitch_keep_alive,
            "voice": self.voice_keep_alive,
        }
        return per_agent_keep_alive.get(normalized) or self.ollama_keep_alive

    def num_ctx_for_agent(self, agent_name: str | None = None) -> int:
        normalized = (agent_name or "").strip().lower()
        per_agent_num_ctx = {
            "daise": self.jarvis_num_ctx,
            "jarvis": self.jarvis_num_ctx,
            "coding": self.coding_num_ctx,
            "lilo": self.lilo_num_ctx,
            "stitch": self.stitch_num_ctx,
            "voice": self.voice_num_ctx,
        }
        return per_agent_num_ctx.get(normalized) or self.ollama_num_ctx

    def num_predict_for_agent(self, agent_name: str | None = None) -> int:
        normalized = (agent_name or "").strip().lower()
        per_agent_num_predict = {
            "daise": self.jarvis_num_predict,
            "jarvis": self.jarvis_num_predict,
            "coding": self.coding_num_predict,
            "lilo": self.lilo_num_predict,
            "stitch": self.stitch_num_predict,
            "voice": self.voice_num_predict,
        }
        return per_agent_num_predict.get(normalized) or self.ollama_num_predict

    @staticmethod
    def _env_model_chain(primary_key: str, fallback_key: str, primary_default: str, fallback_default: str) -> tuple[str, ...]:
        primary = os.getenv(primary_key, primary_default)
        fallbacks = os.getenv(fallback_key, fallback_default)
        extra = tuple(item.strip() for item in fallbacks.split(",") if item.strip())
        chain: list[str] = []
        for candidate in (primary, *extra):
            if candidate and candidate not in chain:
                chain.append(candidate)
        return tuple(chain)

    @staticmethod
    def from_env() -> "ChatSettings":
        default_model = os.getenv("BLACKFONG_OLLAMA_MODEL", "llama3.2:3b")
        default_timeout = float(os.getenv("BLACKFONG_OLLAMA_TIMEOUT", "180"))
        default_chat_model = os.getenv("BLACKFONG_JARVIS_MODEL", os.getenv("BLACKFONG_FAST_MODEL", "llama3.2:3b"))
        default_chat_timeout = float(os.getenv("BLACKFONG_JARVIS_TIMEOUT", "180"))
        default_keep_alive = os.getenv("BLACKFONG_OLLAMA_KEEP_ALIVE", "30m")
        default_num_ctx = int(os.getenv("BLACKFONG_OLLAMA_NUM_CTX", "8192"))
        default_num_predict = int(os.getenv("BLACKFONG_OLLAMA_NUM_PREDICT", "384"))
        fast_chain = ChatSettings._env_model_chain(
            "BLACKFONG_FAST_MODEL",
            "BLACKFONG_FAST_MODEL_FALLBACKS",
            default_chat_model,
            "llama3.2:3b",
        )
        standard_chain = ChatSettings._env_model_chain(
            "BLACKFONG_STANDARD_MODEL",
            "BLACKFONG_STANDARD_MODEL_FALLBACKS",
            default_chat_model,
            fast_chain[0],
        )
        deep_chain = ChatSettings._env_model_chain(
            "BLACKFONG_DEEP_MODEL",
            "BLACKFONG_DEEP_MODEL_FALLBACKS",
            os.getenv("BLACKFONG_DEEP_MODEL", "deepseek-r1:8b"),
            "llama3.2:3b",
        )
        coding_chain = ChatSettings._env_model_chain(
            "BLACKFONG_CODING_MODEL",
            "BLACKFONG_CODING_MODEL_FALLBACKS",
            os.getenv("BLACKFONG_CODING_MODEL", "deepseek-coder:6.7b"),
            "deepseek-coder,llama3.2:3b",
        )
        return ChatSettings(
            backend=os.getenv("BLACKFONG_CHAT_BACKEND", "ollama"),
            daise_backend=os.getenv("BLACKFONG_DAISE_BACKEND", "llamacpp").strip().lower() or "llamacpp",
            daise_model=os.getenv("BLACKFONG_DAISE_MODEL", os.getenv("BLACKFONG_JARVIS_MODEL", "gemma4-heretic:latest")),
            daise_num_predict=int(os.getenv("BLACKFONG_DAISE_NUM_PREDICT", "2048")),
            daise_num_ctx=int(os.getenv("BLACKFONG_DAISE_NUM_CTX", "8192")),
            earth_explanation_model=os.getenv("BLACKFONG_EARTH_EXPLANATION_MODEL", "gemma3:1b-it-qat"),
            earth_explanation_timeout=float(os.getenv("BLACKFONG_EARTH_EXPLANATION_TIMEOUT", "120")),
            earth_explanation_num_predict=int(os.getenv("BLACKFONG_EARTH_EXPLANATION_NUM_PREDICT", "300")),
            earth_evidence_chars=int(os.getenv("BLACKFONG_EARTH_EVIDENCE_CHARS", "6000")),
            ollama_url=os.getenv("BLACKFONG_OLLAMA_URL", "http://localhost:11434"),
            ollama_model=default_model,
            ollama_timeout=default_timeout,
            ollama_keep_alive=default_keep_alive,
            ollama_num_ctx=default_num_ctx,
            ollama_num_predict=default_num_predict,
            fast_model=fast_chain[0],
            fast_model_fallbacks=fast_chain[1:],
            standard_model=standard_chain[0],
            standard_model_fallbacks=standard_chain[1:],
            deep_model=deep_chain[0],
            deep_model_fallbacks=deep_chain[1:],
            fast_timeout=float(os.getenv("BLACKFONG_FAST_TIMEOUT", os.getenv("BLACKFONG_JARVIS_QUICK_TIMEOUT", "60"))),
            fast_keep_alive=os.getenv("BLACKFONG_FAST_KEEP_ALIVE", default_keep_alive),
            fast_num_ctx=int(os.getenv("BLACKFONG_FAST_NUM_CTX", "4096")),
            fast_num_predict=int(os.getenv("BLACKFONG_FAST_NUM_PREDICT", os.getenv("BLACKFONG_JARVIS_QUICK_NUM_PREDICT", "384"))),
            standard_timeout=float(os.getenv("BLACKFONG_STANDARD_TIMEOUT", "90")),
            standard_keep_alive=os.getenv("BLACKFONG_STANDARD_KEEP_ALIVE", default_keep_alive),
            standard_num_ctx=int(os.getenv("BLACKFONG_STANDARD_NUM_CTX", "6144")),
            standard_num_predict=int(os.getenv("BLACKFONG_STANDARD_NUM_PREDICT", "256")),
            deep_timeout=float(os.getenv("BLACKFONG_DEEP_TIMEOUT", str(default_timeout))),
            deep_keep_alive=os.getenv("BLACKFONG_DEEP_KEEP_ALIVE", "20m"),
            deep_num_ctx=int(os.getenv("BLACKFONG_DEEP_NUM_CTX", str(default_num_ctx))),
            deep_num_predict=int(os.getenv("BLACKFONG_DEEP_NUM_PREDICT", "512")),
            fast_prompt_budget_tokens=int(os.getenv("BLACKFONG_FAST_PROMPT_BUDGET_TOKENS", os.getenv("BLACKFONG_QUICK_PROMPT_BUDGET_TOKENS", "600"))),
            standard_prompt_budget_tokens=int(os.getenv("BLACKFONG_STANDARD_PROMPT_BUDGET_TOKENS", "1800")),
            deep_prompt_budget_tokens=int(os.getenv("BLACKFONG_DEEP_PROMPT_BUDGET_TOKENS", "4096")),
            fast_global_memory_limit=int(os.getenv("BLACKFONG_FAST_GLOBAL_MEMORY_LIMIT", "2")),
            standard_global_memory_limit=int(os.getenv("BLACKFONG_STANDARD_GLOBAL_MEMORY_LIMIT", "4")),
            jarvis_model=default_chat_model,
            jarvis_timeout=default_chat_timeout,
            jarvis_keep_alive=os.getenv("BLACKFONG_JARVIS_KEEP_ALIVE", default_keep_alive),
            jarvis_num_ctx=int(os.getenv("BLACKFONG_JARVIS_NUM_CTX", str(default_num_ctx))),
            jarvis_num_predict=int(os.getenv("BLACKFONG_JARVIS_NUM_PREDICT", "256")),
            jarvis_quick_timeout=float(os.getenv("BLACKFONG_JARVIS_QUICK_TIMEOUT", os.getenv("BLACKFONG_FAST_TIMEOUT", "60"))),
            jarvis_quick_num_predict=int(os.getenv("BLACKFONG_JARVIS_QUICK_NUM_PREDICT", os.getenv("BLACKFONG_FAST_NUM_PREDICT", "384"))),
            quick_prompt_budget_tokens=int(os.getenv("BLACKFONG_QUICK_PROMPT_BUDGET_TOKENS", os.getenv("BLACKFONG_FAST_PROMPT_BUDGET_TOKENS", "600"))),
            coding_model=coding_chain[0],
            coding_model_fallbacks=coding_chain[1:],
            coding_timeout=float(os.getenv("BLACKFONG_CODING_TIMEOUT", str(default_timeout))),
            coding_keep_alive=os.getenv("BLACKFONG_CODING_KEEP_ALIVE", default_keep_alive),
            coding_num_ctx=int(os.getenv("BLACKFONG_CODING_NUM_CTX", "8192")),
            coding_num_predict=int(os.getenv("BLACKFONG_CODING_NUM_PREDICT", "512")),
            lilo_model=os.getenv("BLACKFONG_LILO_MODEL", default_chat_model),
            lilo_timeout=float(os.getenv("BLACKFONG_LILO_TIMEOUT", str(default_chat_timeout))),
            lilo_keep_alive=os.getenv("BLACKFONG_LILO_KEEP_ALIVE", "20m"),
            lilo_num_ctx=int(os.getenv("BLACKFONG_LILO_NUM_CTX", str(default_num_ctx))),
            lilo_num_predict=int(os.getenv("BLACKFONG_LILO_NUM_PREDICT", "320")),
            stitch_model=os.getenv("BLACKFONG_STITCH_MODEL", default_chat_model),
            stitch_timeout=float(os.getenv("BLACKFONG_STITCH_TIMEOUT", str(default_chat_timeout))),
            stitch_keep_alive=os.getenv("BLACKFONG_STITCH_KEEP_ALIVE", "20m"),
            stitch_num_ctx=int(os.getenv("BLACKFONG_STITCH_NUM_CTX", str(default_num_ctx))),
            stitch_num_predict=int(os.getenv("BLACKFONG_STITCH_NUM_PREDICT", "320")),
            voice_model=os.getenv("BLACKFONG_VOICE_MODEL", default_chat_model),
            voice_timeout=float(os.getenv("BLACKFONG_VOICE_TIMEOUT", str(default_chat_timeout))),
            voice_keep_alive=os.getenv("BLACKFONG_VOICE_KEEP_ALIVE", "20m"),
            voice_num_ctx=int(os.getenv("BLACKFONG_VOICE_NUM_CTX", "4096")),
            voice_num_predict=int(os.getenv("BLACKFONG_VOICE_NUM_PREDICT", "192")),
            transcript_window=int(os.getenv("BLACKFONG_TRANSCRIPT_WINDOW", "16")),
            global_memory_limit=int(os.getenv("BLACKFONG_GLOBAL_MEMORY_LIMIT", "6")),
            summary_cadence=int(os.getenv("BLACKFONG_SUMMARY_CADENCE", "6")),
        )


@dataclass(frozen=True)
class HUDSettings:
    """Default settings for the Thoughtglass HUD."""

    host: str = "127.0.0.1"
    port: int = 8080
    open_browser: bool = True
    windows_toasts_enabled: bool = True

    @staticmethod
    def from_env() -> "HUDSettings":
        return HUDSettings(
            host=os.getenv("BLACKFONG_HUD_HOST", "127.0.0.1"),
            port=int(os.getenv("BLACKFONG_HUD_PORT", "8080")),
            open_browser=os.getenv("BLACKFONG_HUD_OPEN_BROWSER", "true").lower() != "false",
            windows_toasts_enabled=os.getenv("BLACKFONG_WINDOWS_TOASTS_ENABLED", "true").lower() != "false",
        )


@dataclass(frozen=True)
class LegionSettings:
    """Local-only Legion cockpit pairing and device history settings."""

    pairing_token: str = ""
    session_ttl_hours: int = 720
    sentinel_agent_token: str = ""
    device_history_limit: int = 120
    device_stale_after_seconds: float = 90.0

    @staticmethod
    def from_env() -> "LegionSettings":
        return LegionSettings(
            pairing_token=os.getenv("BLACKFONG_LEGION_PAIRING_TOKEN", "").strip(),
            session_ttl_hours=int(os.getenv("BLACKFONG_LEGION_SESSION_TTL_HOURS", "720")),
            sentinel_agent_token=os.getenv("BLACKFONG_SENTINEL_AGENT_TOKEN", "").strip(),
            device_history_limit=int(os.getenv("BLACKFONG_DEVICE_HISTORY_LIMIT", "120")),
            device_stale_after_seconds=float(os.getenv("BLACKFONG_DEVICE_STALE_AFTER_SECONDS", "90")),
        )


@dataclass(frozen=True)
class TinyProgrammerSettings:
    """Settings for the TinyProgrammer companion node."""

    enabled: bool = True
    base_url: str = "http://127.0.0.1:5000"
    timeout: float = 5.0
    project_path: Path = Path()
    python_executable: str = ""
    launch_timeout: float = 20.0
    auto_start: bool = False
    auto_stream: bool = True
    adopt_external: bool = True
    shutdown_policy: str = "ask"

    @staticmethod
    def from_env(root: Path) -> "TinyProgrammerSettings":
        default_path = root.parent / "TinyProgrammer-main"
        return TinyProgrammerSettings(
            enabled=os.getenv("BLACKFONG_TINYPROGRAMMER_ENABLED", "true").lower() != "false",
            base_url=os.getenv("BLACKFONG_TINYPROGRAMMER_URL", "http://127.0.0.1:5000"),
            timeout=float(os.getenv("BLACKFONG_TINYPROGRAMMER_TIMEOUT", "5")),
            project_path=Path(os.getenv("BLACKFONG_TINYPROGRAMMER_PATH", str(default_path))),
            python_executable=os.getenv("BLACKFONG_TINYPROGRAMMER_PYTHON", ""),
            launch_timeout=float(os.getenv("BLACKFONG_TINYPROGRAMMER_LAUNCH_TIMEOUT", "20")),
            auto_start=os.getenv("BLACKFONG_TINYPROGRAMMER_AUTO_START", "false").lower() == "true",
            auto_stream=os.getenv("BLACKFONG_TINYPROGRAMMER_AUTO_STREAM", "true").lower() != "false",
            adopt_external=os.getenv("BLACKFONG_TINYPROGRAMMER_ADOPT_EXTERNAL", "true").lower() != "false",
            shutdown_policy=os.getenv("BLACKFONG_TINYPROGRAMMER_SHUTDOWN_POLICY", "ask").strip().lower() or "ask",
        )


@dataclass(frozen=True)
class TUISettings:
    """Timing and heartbeat settings for the Blackfong TUI."""

    heartbeat_interval: float = 0.6
    status_refresh_interval: float = 2.5

    @staticmethod
    def from_env() -> "TUISettings":
        return TUISettings(
            heartbeat_interval=float(os.getenv("BLACKFONG_TUI_HEARTBEAT_INTERVAL", "0.6")),
            status_refresh_interval=float(os.getenv("BLACKFONG_TUI_STATUS_REFRESH_INTERVAL", "2.5")),
        )


@dataclass(frozen=True)
class TelegramSettings:
    """Runtime settings for the private Telegram transport adapter."""

    enabled: bool = False
    bot_token: str = ""
    allowlisted_identities: tuple[tuple[str, str], ...] = ()
    private_chat_only: bool = True
    poll_timeout_seconds: float = 20.0
    poll_retry_delay_seconds: float = 2.0
    poll_limit: int = 20
    request_timeout_seconds: float = 30.0
    voice_enabled: bool = True
    max_voice_download_bytes: int = 10_000_000
    max_voice_duration_seconds: float = 120.0
    max_voice_processing_seconds: float = 60.0
    max_transcript_chars: int = 4000

    @staticmethod
    def _parse_allowlist(raw: str) -> tuple[tuple[str, str], ...]:
        entries: list[tuple[str, str]] = []
        for item in raw.replace(";", ",").split(","):
            chunk = item.strip()
            if not chunk or ":" not in chunk:
                continue
            chat_id, user_id = chunk.split(":", 1)
            normalized = (chat_id.strip(), user_id.strip())
            if normalized[0] and normalized[1] and normalized not in entries:
                entries.append(normalized)
        return tuple(entries)

    @staticmethod
    def from_env() -> "TelegramSettings":
        return TelegramSettings(
            enabled=os.getenv("BLACKFONG_TELEGRAM_ENABLED", "false").lower() == "true",
            bot_token=os.getenv("BLACKFONG_TELEGRAM_BOT_TOKEN", "").strip(),
            allowlisted_identities=TelegramSettings._parse_allowlist(
                os.getenv("BLACKFONG_TELEGRAM_ALLOWLIST", "")
            ),
            private_chat_only=os.getenv("BLACKFONG_TELEGRAM_PRIVATE_CHAT_ONLY", "true").lower() != "false",
            poll_timeout_seconds=float(os.getenv("BLACKFONG_TELEGRAM_POLL_TIMEOUT_SECONDS", "20")),
            poll_retry_delay_seconds=float(os.getenv("BLACKFONG_TELEGRAM_POLL_RETRY_DELAY_SECONDS", "2")),
            poll_limit=max(1, min(100, int(os.getenv("BLACKFONG_TELEGRAM_POLL_LIMIT", "20")))),
            request_timeout_seconds=float(os.getenv("BLACKFONG_TELEGRAM_REQUEST_TIMEOUT_SECONDS", "30")),
            voice_enabled=os.getenv("BLACKFONG_TELEGRAM_VOICE_ENABLED", "true").lower() != "false",
            max_voice_download_bytes=int(os.getenv("BLACKFONG_TELEGRAM_MAX_VOICE_DOWNLOAD_BYTES", "10000000")),
            max_voice_duration_seconds=float(os.getenv("BLACKFONG_TELEGRAM_MAX_VOICE_DURATION_SECONDS", "120")),
            max_voice_processing_seconds=float(os.getenv("BLACKFONG_TELEGRAM_MAX_VOICE_PROCESSING_SECONDS", "60")),
            max_transcript_chars=int(os.getenv("BLACKFONG_TELEGRAM_MAX_TRANSCRIPT_CHARS", "4000")),
        )


@dataclass(frozen=True)
class GPSSettings:
    """Runtime settings for shared GPS/GNSS awareness."""

    enabled: bool = True
    provider_mode: str = "auto"
    serial_port: str = ""
    baud_rate: int = 9600
    simulator_enabled: bool = True
    simulator_step_seconds: float = 3.0
    emit_interval_seconds: float = 4.0
    trail_limit: int = 48
    route_history_limit: int = 240

    @staticmethod
    def from_env() -> "GPSSettings":
        provider_mode = (os.getenv("BLACKFONG_GPS_PROVIDER", "auto").strip().lower() or "auto")
        if provider_mode not in {"auto", "serial", "simulator", "off"}:
            provider_mode = "auto"
        return GPSSettings(
            enabled=os.getenv("BLACKFONG_GPS_ENABLED", "true").lower() != "false",
            provider_mode=provider_mode,
            serial_port=os.getenv("BLACKFONG_GPS_SERIAL_PORT", "").strip(),
            baud_rate=max(1200, int(os.getenv("BLACKFONG_GPS_BAUD_RATE", "9600"))),
            simulator_enabled=os.getenv("BLACKFONG_GPS_SIMULATOR_ENABLED", "true").lower() != "false",
            simulator_step_seconds=max(0.5, float(os.getenv("BLACKFONG_GPS_SIMULATOR_STEP_SECONDS", "3"))),
            emit_interval_seconds=max(1.0, float(os.getenv("BLACKFONG_GPS_EMIT_INTERVAL_SECONDS", "4"))),
            trail_limit=max(8, min(256, int(os.getenv("BLACKFONG_GPS_TRAIL_LIMIT", "48")))),
            route_history_limit=max(32, min(1000, int(os.getenv("BLACKFONG_GPS_ROUTE_HISTORY_LIMIT", "240")))),
        )


paths = Paths.from_root(Path(__file__).resolve().parents[1])
identity = SystemIdentity()
chat_settings = ChatSettings.from_env()
llamacpp_settings = LlamaCppSettings.from_env(paths.root)
knowledge_settings = KnowledgeSettings.from_env()
hud_settings = HUDSettings.from_env()
legion_settings = LegionSettings.from_env()
tinyprogrammer_settings = TinyProgrammerSettings.from_env(paths.root)
tui_settings = TUISettings.from_env()
telegram_settings = TelegramSettings.from_env()
gps_settings = GPSSettings.from_env()

