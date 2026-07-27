from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class FileChatIntent:
    action: str
    path: Optional[str] = None
    proposal_id: Optional[str] = None
    proposed_content: Optional[str] = None
    summary: str = ""
    typed_confirmation: Optional[str] = None


_PROPOSAL_ID_PATTERN = re.compile(r"\bproposal\s+([a-z0-9_-]{6,24})\b", re.IGNORECASE)
_ROLLBACK_PHRASE_PATTERN = re.compile(r"\bROLLBACK\s+([A-Za-z0-9_-]{6,24})\b")
_CODE_BLOCK_PATTERN = re.compile(r"```(?:[^\n`]*)\n([\s\S]*?)```", re.MULTILINE)
_WINDOWS_PATH_PATTERN = re.compile(r"([A-Za-z]:\\[^\n`\"]+)")
_RELATIVE_PATH_PATTERN = re.compile(r"((?:\.{1,2}[\\/]|[\w .-]+[\\/])+[\w .-]+)")


def parse_file_chat_intent(user_input: str) -> Optional[FileChatIntent]:
    text = str(user_input or "").strip()
    lower = text.lower()
    if not text:
        return None

    if _looks_like_policy_request(lower):
        return FileChatIntent(action="policy")

    if _looks_like_proposal_list_request(lower):
        return FileChatIntent(action="proposals")

    if _looks_like_roots_request(lower):
        return FileChatIntent(action="roots")

    proposal_id = _extract_proposal_id(text)
    typed_confirmation = _extract_typed_confirmation(text)
    if proposal_id:
        if any(token in lower for token in ("rollback", "revert")):
            return FileChatIntent(action="rollback", proposal_id=proposal_id, typed_confirmation=typed_confirmation)
        if any(token in lower for token in ("approve", "accept")):
            return FileChatIntent(action="approve", proposal_id=proposal_id)
        if any(token in lower for token in ("validate", "check")):
            return FileChatIntent(action="validate", proposal_id=proposal_id)
        if any(token in lower for token in ("apply", "commit")):
            return FileChatIntent(action="apply", proposal_id=proposal_id)

    explicit_path = _extract_path(text)

    if explicit_path and _looks_like_root_approval_request(lower):
        return FileChatIntent(action="approve_root", path=explicit_path)

    if explicit_path and _looks_like_map_request(lower):
        return FileChatIntent(action="map", path=explicit_path)

    if _looks_like_scan_request(lower):
        return FileChatIntent(action="scan", path=explicit_path or ".")

    if explicit_path and _looks_like_read_request(lower):
        return FileChatIntent(action="read", path=explicit_path)

    if explicit_path and _looks_like_propose_request(lower):
        proposed_content = _extract_proposed_content(text)
        return FileChatIntent(
            action="propose",
            path=explicit_path,
            proposed_content=proposed_content,
            summary=_build_summary(text, explicit_path),
        )

    return None


def _looks_like_policy_request(lower: str) -> bool:
    return "file policy" in lower or ("workspace policy" in lower and "file" in lower)


def _looks_like_proposal_list_request(lower: str) -> bool:
    return "file proposals" in lower or "governed proposals" in lower or ("proposal" in lower and any(token in lower for token in ("list", "show", "what are", "display")))


def _looks_like_roots_request(lower: str) -> bool:
    phrases = (
        "file roots",
        "scope roots",
        "approved roots",
        "approved scopes",
        "show roots",
        "list roots",
        "show drives",
        "list drives",
    )
    return any(phrase in lower for phrase in phrases)


def _looks_like_root_approval_request(lower: str) -> bool:
    return any(phrase in lower for phrase in ("approve root", "approve scope", "add root", "add scope", "allow root", "allow scope"))


def _looks_like_scan_request(lower: str) -> bool:
    if any(phrase in lower for phrase in ("scan workspace", "scan folder", "scan directory", "list files", "show files", "browse files")):
        return True
    return any(token in lower for token in ("scan", "browse", "list")) and any(token in lower for token in ("folder", "directory", "workspace", "files"))


def _looks_like_map_request(lower: str) -> bool:
    if any(phrase in lower for phrase in ("map folder", "map directory", "browse folder", "browse directory", "show tree", "show folder tree")):
        return True
    return " map " in f" {lower} " or ("browse" in lower and "file" not in lower)


def _looks_like_read_request(lower: str) -> bool:
    return any(token in lower for token in ("read", "show", "open", "display")) and "proposal" not in lower


def _looks_like_propose_request(lower: str) -> bool:
    return any(token in lower for token in ("create", "write", "save", "edit", "update", "change", "propose"))


def _extract_proposal_id(text: str) -> Optional[str]:
    rollback = _ROLLBACK_PHRASE_PATTERN.search(text)
    if rollback:
        return rollback.group(1)
    match = _PROPOSAL_ID_PATTERN.search(text)
    if match:
        return match.group(1)
    fallback = re.search(r"\b(?:approve|apply|validate|rollback|revert)\s+([a-z0-9_-]{6,24})\b", text, re.IGNORECASE)
    if fallback:
        return fallback.group(1)
    return None


def _extract_typed_confirmation(text: str) -> Optional[str]:
    match = _ROLLBACK_PHRASE_PATTERN.search(text)
    if match:
        return f"ROLLBACK {match.group(1)}"
    return None


def _extract_path(text: str) -> Optional[str]:
    for candidate in re.findall(r"`([^`]+)`", text):
        if _looks_like_path(candidate):
            return candidate.strip()

    for candidate in re.findall(r"\"([^\"]+)\"", text):
        if _looks_like_path(candidate):
            return candidate.strip()

    for candidate in re.findall(r"'([^']+)'", text):
        if _looks_like_path(candidate):
            return candidate.strip()

    windows = _WINDOWS_PATH_PATTERN.search(text)
    if windows and _looks_like_path(windows.group(1)):
        return windows.group(1).strip()

    relative = _RELATIVE_PATH_PATTERN.search(text)
    if relative and _looks_like_path(relative.group(1)):
        return relative.group(1).strip()

    return None


def _extract_proposed_content(text: str) -> Optional[str]:
    blocks = _CODE_BLOCK_PATTERN.findall(text)
    if blocks:
        return blocks[-1].strip("\n")

    content_match = re.search(r"\bwith content:\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if content_match:
        return content_match.group(1).strip()

    body_match = re.search(r"\b(?:create|write|save|edit|update|change|propose)\b.+?\bfile\b.+?:\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if body_match:
        return body_match.group(1).strip()

    return None


def _build_summary(text: str, path: str) -> str:
    normalized = text.replace(path, "").replace(f"`{path}`", "")
    normalized = _CODE_BLOCK_PATTERN.sub("", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" :-")
    return normalized[:180] if normalized else f"Governed proposal for {path}"


def _looks_like_path(candidate: str) -> bool:
    text = str(candidate or "").strip()
    if not text:
        return False
    if text.startswith(("http://", "https://")):
        return False
    if "\\" in text or "/" in text:
        return True
    if re.match(r"^[\w .-]+\.[A-Za-z0-9]{1,12}$", text):
        return True
    return bool(re.match(r"^[A-Za-z]:\\", text))
