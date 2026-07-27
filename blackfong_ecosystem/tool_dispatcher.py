"""
Tool Dispatcher

Routes tool requests from agents to the appropriate services.
Handles tool invocation, response collection, and error handling.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .agent import ToolRequest, ToolResponse, ToolStatus
from .event_client import EventClient
from .service_registry import get_registry


class ToolDispatcher:
    """Routes tool requests to registered services."""
    
    def __init__(self, event_client: Optional[EventClient] = None):
        self.registry = get_registry()
        self.event_client = event_client
    
    def can_handle(self, tool_name: str, action: str) -> bool:
        """Check if a tool/action pair can be handled."""
        service = self.registry.get_service(tool_name)
        if not service:
            return False
        return service.get_capability(action) is not None
    
    def dispatch(
        self,
        request: ToolRequest,
    ) -> ToolResponse:
        """
        Dispatch a tool request to the appropriate service.
        
        Args:
            request: ToolRequest with tool_name, action, params
        
        Returns:
            ToolResponse with result or error
        """
        try:
            # Get service from registry
            service = self.registry.get_service(request.tool_name)
            if not service:
                return ToolResponse(
                    request_id=request.request_id,
                    success=False,
                    error=f"Service not found: {request.tool_name}",
                    status=ToolStatus.FAILED,
                )
            
            # Get capability
            capability = service.get_capability(request.action)
            if not capability:
                return ToolResponse(
                    request_id=request.request_id,
                    success=False,
                    error=f"Action not found: {request.action} on {request.tool_name}",
                    status=ToolStatus.FAILED,
                )
            
            # Check service is available
            if not service.is_available:
                return ToolResponse(
                    request_id=request.request_id,
                    success=False,
                    error=f"Service not available: {request.tool_name}",
                    status=ToolStatus.FAILED,
                )
            
            # For now, simulate successful tool invocation
            # In Phase 3, this will actually call the service
            result = {
                "tool": request.tool_name,
                "action": request.action,
                "params_received": request.params,
                "status": "simulated_success",
            }
            
            return ToolResponse(
                request_id=request.request_id,
                success=True,
                result=result,
                status=ToolStatus.SUCCESS,
            )
        
        except Exception as e:
            return ToolResponse(
                request_id=request.request_id,
                success=False,
                error=str(e),
                status=ToolStatus.FAILED,
            )
    
    def list_available_tools(self) -> Dict[str, Any]:
        """List all available tools and their capabilities."""
        result = {}
        for service in self.registry.list_services():
            service_dict = {
                "type": service.service_type,
                "endpoint": service.endpoint,
                "available": service.is_available,
                "capabilities": [c.to_dict() for c in service.capabilities],
            }
            result[service.name] = service_dict
        return result
