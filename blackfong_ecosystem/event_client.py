"""
Event Client Library

Simple HTTP client for external systems to publish events to O.B.E.O.S.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


class EventClient:
    """Client for publishing events to the O.B.E.O.S event API."""
    
    def __init__(self, api_url: str = "http://localhost:8000"):
        self.api_url = api_url.rstrip("/")
        self.client = httpx.Client(timeout=10)
    
    def publish_event(
        self,
        source: str,
        event_type: str,
        payload: Dict[str, Any],
        confidence: float = 1.0,
        parent_event: Optional[str] = None,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        tags: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Publish an event to the API.
        
        Args:
            source: Source system (e.g., "transcriber", "vision")
            event_type: Type of event (e.g., "transcription", "object_detection")
            payload: Event data
            confidence: Confidence score (0-1)
            parent_event: ID of parent event (for causality)
            session_id: Session ID (for grouping)
            correlation_id: Correlation ID (for workflows)
            tags: Tags for categorization
        
        Returns:
            API response dict
        """
        request_data = {
            "source": source,
            "event_type": event_type,
            "payload": payload,
            "confidence": confidence,
        }
        
        if parent_event:
            request_data["parent_event"] = parent_event
        if session_id:
            request_data["session_id"] = session_id
        if correlation_id:
            request_data["correlation_id"] = correlation_id
        if tags:
            request_data["tags"] = tags
        
        response = self.client.post(f"{self.api_url}/events/publish", json=request_data)
        response.raise_for_status()
        return response.json()
    
    def query_events(
        self,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query events with optional filters.
        
        Args:
            event_type: Filter by event type
            source: Filter by source
            limit: Max results
            session_id: Filter by session ID
            correlation_id: Filter by correlation ID
        
        Returns:
            API response dict with events list
        """
        params = {"limit": limit}
        
        if event_type:
            params["event_type"] = event_type
        if source:
            params["source"] = source
        if session_id:
            params["session_id"] = session_id
        if correlation_id:
            params["correlation_id"] = correlation_id
        
        response = self.client.get(f"{self.api_url}/events/query", params=params)
        response.raise_for_status()
        return response.json()
    
    def get_event(self, event_id: str) -> Dict[str, Any]:
        """Get a specific event by ID."""
        response = self.client.get(f"{self.api_url}/events/{event_id}")
        response.raise_for_status()
        return response.json()
    
    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()
    
    def __enter__(self) -> EventClient:
        return self
    
    def __exit__(self, *args: Any) -> None:
        self.close()
