"""
Legacy JARVIS-MARK-5 to O.B.E.O.S Bridge Layer (unused: nothing in the
codebase imports this module; kept only as a historical standalone CLI).

D.A.I.S.E. is the canonical brain; "jarvis"/"JARVIS-MARK-5" survives here
only as the legacy identifier this bridge was written to be compatible
with. Per the project's identity doctrine, operator-facing text should
never present as "Jarvis" - so the console banner, prompt and help text
below were updated to D.A.I.S.E. even though the module itself is dead.

Compatibility wrapper that allows the legacy JARVIS-MARK-5 CLI to use
O.B.E.O.S as the intelligence backend while maintaining API compatibility.

This is the gradual migration path:
- D.A.I.S.E. (formerly JARVIS) is the interface/CLI
- O.B.E.O.S becomes the control plane
- Events provide observability
- Backward compatible queries still work
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Any, Optional

from blackfong_ecosystem.unified_orchestrator import unified_orchestrator, InputModality
from blackfong_ecosystem.observability import log_action, LogLevel


class DAISEBridge:
    """
    Compatibility layer between the legacy JARVIS-MARK-5 CLI and O.B.E.O.S.

    Routes JARVIS-MARK-5 queries through O.B.E.O.S while maintaining
    backward compatibility with existing JARVIS-MARK-5 code.
    """

    def __init__(self):
        self.orchestrator = unified_orchestrator
        self.query_count = 0

    def respond(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        JARVIS-MARK-5-compatible respond() method.

        Routes through O.B.E.O.S and returns string response.
        """

        # Detect query type
        modality = self._detect_modality(prompt)

        # Process through O.B.E.O.S
        result = self.orchestrator.process_input(
            prompt,
            modality=modality,
            context=context
        )

        self.query_count += 1

        # Log response
        log_action(
            LogLevel.INFO,
            "daise_bridge",
            "respond_completed",
            query_count=self.query_count,
            agent=result.get("agent"),
            confidence=result.get("confidence")
        )

        # Return just the response text for JARVIS-MARK-5 compatibility
        return result.get("response", "No response generated")
    
    def _detect_modality(self, prompt: str) -> InputModality:
        """Detect input modality from prompt"""
        
        prompt_lower = prompt.lower()
        
        if any(keyword in prompt_lower for keyword in 
               ["```", "code", "function", "class", "import", "debug"]):
            return InputModality.CODE
        
        elif any(keyword in prompt_lower for keyword in 
                 ["image", "photo", "vision", "detect"]):
            return InputModality.VISION
        
        elif any(keyword in prompt_lower for keyword in 
                 ["voice", "audio", "transcribe"]):
            return InputModality.VOICE
        
        else:
            return InputModality.TEXT
    
    def process_query(self, query: str) -> Dict[str, Any]:
        """
        Extended process_query() method that returns full result dict.
        
        For callers that want the full response object with metadata.
        """
        
        modality = self._detect_modality(query)
        result = self.orchestrator.process_input(query, modality=modality)
        
        return result
    
    def run_repl(self):
        """
        JARVIS-MARK-5-compatible REPL that dispatches to O.B.E.O.S.
        """
        print("\n" + "="*70)
        print("🤖 D.A.I.S.E. (Powered by O.B.E.O.S, legacy JARVIS-MARK-5 bridge)")
        print("="*70)
        print("Status: Connected to O.B.E.O.S unified orchestrator")
        print("Commands: help, status, stress, exit\n")

        while True:
            try:
                user_input = input("daise> ").strip()
                
                if not user_input:
                    continue
                
                elif user_input == "exit":
                    print("Shutting down...")
                    break
                
                elif user_input == "help":
                    self._print_help()
                
                elif user_input == "status":
                    self._print_status()
                
                elif user_input == "stress":
                    print("Starting stress test Level 1...")
                    from blackfong_ecosystem.stress_testing_suite import StressTestSuite
                    suite = StressTestSuite()
                    suite.run_level_1()
                
                else:
                    response = self.respond(user_input)
                    print(f"\n{response}\n")
            
            except KeyboardInterrupt:
                print("\n\nInterrupt received. Type 'exit' to quit.")
            except Exception as e:
                print(f"Error: {e}")
    
    def _print_help(self):
        """Print help compatible with JARVIS-MARK-5"""
        print("\nD.A.I.S.E. Commands:")
        print("  <query>     - Ask a question (routed to O.B.E.O.S)")
        print("  status      - Show system status")
        print("  stress      - Run stress test")
        print("  help        - Show this help message")
        print("  exit        - Exit the system\n")

    def _print_status(self):
        """Print status"""
        print(f"\n✅ D.A.I.S.E. Status:")
        print(f"  Queries processed: {self.query_count}")
        print(f"  Backend: O.B.E.O.S Unified Orchestrator")
        print(f"  Status: Operational")
        print()


# Global bridge instance. Kept as the name legacy callers of this unused,
# unimported module would have used; D.A.I.S.E. is the current identity.
JARVIStoOBEOSBridge = DAISEBridge
jarvis_bridge = DAISEBridge()


if __name__ == "__main__":
    jarvis_bridge.run_repl()
