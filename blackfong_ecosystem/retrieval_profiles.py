from __future__ import annotations

from typing import Any, Dict, Optional


RETRIEVAL_PROFILE_ALIASES = {
    "quick": "fast",
    "fast": "fast",
    "standard": "standard",
    "normal": "standard",
    "default": "standard",
    "deep": "deep",
    "thorough": "deep",
    "architecture": "architecture",
}

RETRIEVAL_PROFILES: Dict[str, Dict[str, Any]] = {
    "fast": {
        "vector_k": 0,
        "citation_limit": 0,
        "context_chars": 0,
        "graph_limit": 0,
        "include_semantic": False,
        "include_graph": False,
        "include_missing_links": False,
        "description": "Minimal retrieval. Prioritize prompt compactness and latency.",
    },
    "standard": {
        "vector_k": 4,
        "citation_limit": 4,
        "context_chars": 1400,
        "graph_limit": 4,
        "include_semantic": True,
        "include_graph": False,
        "include_missing_links": False,
        "description": "Bounded semantic retrieval with concise provenance.",
    },
    "deep": {
        "vector_k": 6,
        "citation_limit": 6,
        "context_chars": 2200,
        "graph_limit": 4,
        "neighborhood_centers": 2,
        "neighborhood_limit": 6,
        "include_semantic": True,
        "include_graph": True,
        "include_missing_links": False,
        "include_clusters": False,
        "include_recent_deltas": False,
        "include_reasoning_path": False,
        "description": "Semantic retrieval plus graph reasoning and richer provenance.",
    },
    "architecture": {
        "vector_k": 6,
        "citation_limit": 6,
        "context_chars": 2400,
        "graph_limit": 4,
        "neighborhood_centers": 2,
        "neighborhood_limit": 6,
        "include_semantic": True,
        "include_graph": True,
        "include_missing_links": False,
        "include_clusters": False,
        "include_recent_deltas": False,
        "include_reasoning_path": False,
        "architecture_focus": True,
        "description": "Architecture-focused retrieval for topology, contracts, and graph-oriented reasoning.",
    },
}


def normalize_retrieval_profile(profile: Optional[str], analysis_mode: Optional[str] = None) -> str:
    for raw in (profile, analysis_mode, "fast"):
        normalized = RETRIEVAL_PROFILE_ALIASES.get(str(raw or "").strip().lower())
        if normalized:
            return normalized
    return "fast"


def retrieval_profile_settings(profile: Optional[str], analysis_mode: Optional[str] = None) -> Dict[str, Any]:
    normalized = normalize_retrieval_profile(profile, analysis_mode)
    settings = dict(RETRIEVAL_PROFILES.get(normalized, RETRIEVAL_PROFILES["fast"]))
    settings["name"] = normalized
    return settings
