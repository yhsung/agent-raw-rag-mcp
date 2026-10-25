"""Configuration loader and environment manager for agent-raw-rag-mcp."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
import yaml

# Architectural Invariant: Enforce Hugging Face model cache on dedicated drive
DEFAULT_HF_HOME = "/Volumes/Samsung980/hf-home/"
if "HF_HOME" not in os.environ:
    os.environ["HF_HOME"] = DEFAULT_HF_HOME


@dataclass
class EnvironmentConfig:
    hf_home: str = DEFAULT_HF_HOME
    backend: str = "mlx"


@dataclass
class ChunkingConfig:
    encoding_name: str = "cl100k_base"
    chunk_size: int = 256
    chunk_overlap: int = 32
    expand_turns_on_display: bool = True
    dedup_turns_on_display: bool = True

    @property
    def step(self) -> int:
        return self.chunk_size - self.chunk_overlap


@dataclass
class SanitizerConfig:
    keep_user_text_full: bool = True
    keep_assistant_text_full: bool = True
    max_thinking_tokens: int = 512
    failed_bash_tail_lines: int = 15
    strip_read_file_stdout: bool = True
    strip_full_diffs: bool = True


@dataclass
class DynamicTopKConfig:
    enabled: bool = False
    score_threshold: float = 0.5
    fallback_top_k: int = 3


@dataclass
class RetrievalConfig:
    embedding_model: str = "mlx-community/Qwen3-Embedding-4B-4bit-DWQ"
    reranker_model: str = "mlx-community/Qwen3-Reranker-0.6B-4bit"
    candidate_top_k: int = 128
    rerank_top_k: int = 15
    chronological_reorder: bool = True
    dynamic_top_k: DynamicTopKConfig = field(default_factory=DynamicTopKConfig)


@dataclass
class SourceItemConfig:
    enabled: bool = True
    path: str = ""


@dataclass
class SourcesConfig:
    claude_code: SourceItemConfig = field(
        default_factory=lambda: SourceItemConfig(enabled=True, path="~/.claude/projects")
    )
    codex: SourceItemConfig = field(
        default_factory=lambda: SourceItemConfig(enabled=True, path="~/.codex/sessions")
    )
    opencode: SourceItemConfig = field(
        default_factory=lambda: SourceItemConfig(
            enabled=True, path="~/.local/share/opencode/opencode.db"
        )
    )
    oh_my_pi: SourceItemConfig = field(
        default_factory=lambda: SourceItemConfig(
            enabled=True, path="~/.omp/agent/sessions"
        )
    )


@dataclass
class StorageConfig:
    backend: str = "sqlite"
    sqlite_path: str = "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/data/raw_rag.db"


@dataclass
class AppConfig:
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    sanitizer: SanitizerConfig = field(default_factory=SanitizerConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    def resolve_paths(self) -> None:
        """Resolve user homedir shortcuts and ensure storage directory exists."""
        self.storage.sqlite_path = str(
            Path(self.storage.sqlite_path).expanduser().resolve()
        )
        storage_dir = Path(self.storage.sqlite_path).parent
        storage_dir.mkdir(parents=True, exist_ok=True)

        self.sources.claude_code.path = str(
            Path(self.sources.claude_code.path).expanduser()
        )
        self.sources.codex.path = str(Path(self.sources.codex.path).expanduser())
        self.sources.opencode.path = str(Path(self.sources.opencode.path).expanduser())
        self.sources.oh_my_pi.path = str(Path(self.sources.oh_my_pi.path).expanduser())


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load YAML config or return default config."""
    default_locations = [
        config_path,
        os.getenv("RAW_RAG_CONFIG"),
        str(
            Path(
                "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/configs/default_config.yaml"
            )
        ),
        str(Path.cwd() / "configs" / "default_config.yaml"),
    ]

    cfg_dict: Dict[str, Any] = {}
    for loc in default_locations:
        if loc and Path(loc).is_file():
            try:
                with open(loc, "r", encoding="utf-8") as f:
                    cfg_dict = yaml.safe_load(f) or {}
                break
            except Exception:
                continue

    env_cfg = EnvironmentConfig(
        hf_home=cfg_dict.get("environment", {}).get("hf_home", DEFAULT_HF_HOME),
        backend=cfg_dict.get("environment", {}).get("backend", "mlx"),
    )
    os.environ["HF_HOME"] = env_cfg.hf_home

    chunk_d = cfg_dict.get("chunking", {})
    chunk_cfg = ChunkingConfig(
        encoding_name=chunk_d.get("encoding_name", "cl100k_base"),
        chunk_size=chunk_d.get("chunk_size", 256),
        chunk_overlap=chunk_d.get("chunk_overlap", 32),
        expand_turns_on_display=chunk_d.get("expand_turns_on_display", True),
        dedup_turns_on_display=chunk_d.get("dedup_turns_on_display", True),
    )

    san_d = cfg_dict.get("sanitizer", {})
    san_cfg = SanitizerConfig(
        keep_user_text_full=san_d.get("keep_user_text_full", True),
        keep_assistant_text_full=san_d.get("keep_assistant_text_full", True),
        max_thinking_tokens=san_d.get("max_thinking_tokens", 512),
        failed_bash_tail_lines=san_d.get("failed_bash_tail_lines", 15),
        strip_read_file_stdout=san_d.get("strip_read_file_stdout", True),
        strip_full_diffs=san_d.get("strip_full_diffs", True),
    )

    ret_d = cfg_dict.get("retrieval", {})
    dyn_d = ret_d.get("dynamic_top_k", {})
    dyn_cfg = DynamicTopKConfig(
        enabled=dyn_d.get("enabled", False),
        score_threshold=dyn_d.get("score_threshold", 0.5),
        fallback_top_k=dyn_d.get("fallback_top_k", 3),
    )

    # Use existing local MLX models if Qwen default is specified
    raw_emb = ret_d.get("embedding_model", "mlx-community/Qwen3-Embedding-4B-4bit-DWQ")
    if "Qwen/Qwen3-Embedding" in raw_emb:
        raw_emb = "mlx-community/Qwen3-Embedding-4B-4bit-DWQ"

    raw_rerank = ret_d.get("reranker_model", "mlx-community/Qwen3-Reranker-0.6B-4bit")
    if "Qwen/Qwen3-Reranker-4B" in raw_rerank:
        raw_rerank = "mlx-community/Qwen3-Reranker-0.6B-4bit"

    ret_cfg = RetrievalConfig(
        embedding_model=raw_emb,
        reranker_model=raw_rerank,
        candidate_top_k=ret_d.get("candidate_top_k", 128),
        rerank_top_k=ret_d.get("rerank_top_k", 15),
        chronological_reorder=ret_d.get("chronological_reorder", True),
        dynamic_top_k=dyn_cfg,
    )

    src_d = cfg_dict.get("sources", {})
    claude_d = src_d.get("claude_code", {})
    codex_d = src_d.get("codex", {})
    opencode_d = src_d.get("opencode", {})
    omp_d = src_d.get("oh_my_pi", {})

    src_cfg = SourcesConfig(
        claude_code=SourceItemConfig(
            enabled=claude_d.get("enabled", True),
            path=claude_d.get("projects_dir", "~/.claude/projects"),
        ),
        codex=SourceItemConfig(
            enabled=codex_d.get("enabled", True),
            path=codex_d.get("sessions_dir", "~/.codex/sessions"),
        ),
        opencode=SourceItemConfig(
            enabled=opencode_d.get("enabled", True),
            path=opencode_d.get("sqlite_path", "~/.local/share/opencode/opencode.db"),
        ),
        oh_my_pi=SourceItemConfig(
            enabled=omp_d.get("enabled", True),
            path=omp_d.get("sessions_dir", "~/.omp/agent/sessions"),
        ),
    )

    sto_d = cfg_dict.get("storage", {})
    sto_cfg = StorageConfig(
        backend=sto_d.get("backend", "sqlite"),
        sqlite_path=sto_d.get(
            "sqlite_path",
            "/Volumes/Samsung970EVOPlus/dev-projects/agent-raw-rag-mcp/data/raw_rag.db",
        ),
    )

    config = AppConfig(
        environment=env_cfg,
        chunking=chunk_cfg,
        sanitizer=san_cfg,
        retrieval=ret_cfg,
        sources=src_cfg,
        storage=sto_cfg,
    )
    config.resolve_paths()
    return config
