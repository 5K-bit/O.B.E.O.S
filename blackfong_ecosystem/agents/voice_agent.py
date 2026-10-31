"""
Voice Agent for O.B.E.O.S

Specialized agent for voice-driven interactions with system control and app automation.
Integrates speech-to-text, text-to-speech, and safe system command execution.

Reuses JARVIS-MARK-5 voice components for STT/TTS while integrating with O.B.E.O.S event system.
"""

from __future__ import annotations

import os
import time
import uuid
import threading
import queue
import logging
from typing import Any, Dict, List, Optional, Callable
from enum import Enum

from blackfong_ecosystem.agent import Agent, AgentType, ReasoningStep, ReasoningTrace, ToolResponse
from blackfong_ecosystem.event_client import EventClient
from blackfong_ecosystem.events import Event
from blackfong_ecosystem.storage import EventStore
from blackfong_ecosystem.observability import log_action, LogLevel

# Voice Stack imports
try:
    from TTS.api import TTS
    TTS_AVAILABLE = True
except ImportError:
    TTS_AVAILABLE = False
    TTS = None

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    WhisperModel = None


class VoicePersonality(str, Enum):
    """Voice personality presets."""
    CALM = "calm"
    DIRECT = "direct"
    SHARP = "sharp"
    MENTOR = "mentor"


class VoiceTone(str, Enum):
    """Voice tone settings."""
    CALM = "calm"
    DIRECT = "direct"
    SHARP = "sharp"
    MENTOR_LIKE = "mentor-like"


class ResponseStyle(str, Enum):
    """Response style settings."""
    SHORT = "short"
    DETAILED = "detailed"


class VocabularyLevel(str, Enum):
    """Vocabulary complexity settings."""
    TECHNICAL = "technical"
    PLAIN = "plain"


class VoiceMode(str, Enum):
    """Voice interaction modes."""
    PUSH_TO_TALK = "push_to_talk"
    WAKE_WORD = "wake_word"
    ALWAYS_LISTEN = "always_listen"


class VoiceSafetyLevel(str, Enum):
    """Safety levels for voice commands."""
    SAFE = "safe"           # App launching, basic queries
    CONFIRMED = "confirmed" # Requires explicit confirmation
    RESTRICTED = "restricted" # Dangerous operations, blocked


class VoiceAgent(Agent):
    """
    Voice-driven agent for O.B.E.O.S ecosystem.

    Handles:
    - Speech-to-text processing
    - Text-to-speech responses
    - Voice command routing through orchestrator
    - Safe system command execution
    - Voice memory and context management
    """

    WAKE_WORDS = [
        "hey blackfong", "blackfong", "hey obeos", "obeos",
        "hey ai", "okay ai", "listen ai", "wake up ai"
    ]

    # Whitelisted system commands (safe operations only)
    SAFE_COMMANDS = {
        "open_vscode": ["open", "vscode", "code", "editor", "ide"],
        "open_browser": ["open", "browser", "chrome", "firefox", "web"],
        "open_terminal": ["open", "terminal", "cmd", "command", "shell"],
        "take_screenshot": ["screenshot", "capture", "screen"],
        "system_status": ["status", "system", "info", "health"],
    }

    # Commands requiring confirmation
    CONFIRMED_COMMANDS = {
        "shutdown_system": ["shutdown", "power off", "turn off"],
        "restart_system": ["restart", "reboot", "reset"],
    }

    # Action templates for structured command execution
    ACTION_TEMPLATES = {
        "open_code_workspace": {
            "description": "Open a code workspace or project",
            "parameters": ["project_path", "editor"],
            "default_editor": "vscode",
            "implementation": "_open_code_workspace"
        },
        "run_debug_session": {
            "description": "Start debugging a specific file",
            "parameters": ["file_path", "debugger_type"],
            "default_debugger": "python",
            "implementation": "_run_debug_session"
        },
        "launch_browser_with_url": {
            "description": "Open browser with specific URL",
            "parameters": ["url", "browser"],
            "default_browser": "default",
            "implementation": "_launch_browser_with_url"
        },
        "search_and_open_file": {
            "description": "Search for and open a file",
            "parameters": ["filename", "directory"],
            "implementation": "_search_and_open_file"
        },
        "create_new_project": {
            "description": "Create a new project with template",
            "parameters": ["project_type", "project_name", "location"],
            "implementation": "_create_new_project"
        },
        "run_terminal_command": {
            "description": "Execute a safe terminal command",
            "parameters": ["command", "working_directory"],
            "safe_commands": ["git status", "git log", "ls", "dir", "pwd"],
            "implementation": "_run_terminal_command"
        }
    }

    def __init__(
        self,
        event_client: Optional[EventClient] = None,
        event_api_url: str = "http://localhost:8000",
        voice_mode: VoiceMode = VoiceMode.PUSH_TO_TALK,
    ):
        super().__init__(
            name="voice",
            agent_type=AgentType.UTILITY,
            event_client=event_client,
            event_api_url=event_api_url,
        )

        self.session_id = str(uuid.uuid4())
        self.event_store = EventStore()
        self.voice_mode = voice_mode

        # Voice processing components (will integrate JARVIS components)
        self.stt_engine = None
        self.tts_engine = None
        self.audio_stream = None

        # Voice Personality Layer
        self.voice_personality = VoicePersonality.CALM
        self.response_style = ResponseStyle.DETAILED
        self.vocabulary_level = VocabularyLevel.TECHNICAL

        # Coqui TTS configuration
        self.tts_model = None
        self.tts_device = "cpu"  # Default to CPU for compatibility
        self.voice_samples = {}  # Cache for generated voice samples

        # Initialize Coqui TTS
        self._initialize_coqui_tts()

        # Threading and queues
        self.audio_queue = queue.Queue()
        self.command_queue = queue.Queue()
        self.is_listening = False
        self.is_processing = False

        # Voice memory and context
        self.conversation_history: List[Dict[str, Any]] = []
        self.voice_context: Dict[str, Any] = {}

        # Safety and authentication
        self.authenticated = False
        self.last_wake_word_time = 0
        self.wake_word_timeout = 30  # seconds

        # Callbacks
        self.on_voice_command: Optional[Callable[[str], None]] = None
        self.on_system_action: Optional[Callable[[str, Dict[str, Any]], None]] = None

        # Initialize voice components
        self._initialize_voice_components()

        log_action(
            LogLevel.INFO,
            "voice_agent",
            "initialized",
            session_id=self.session_id,
            voice_mode=voice_mode.value
        )

    def _initialize_voice_components(self):
        """Initialize STT and TTS engines by integrating JARVIS components."""
        try:
            # Import and initialize JARVIS voice system
            import sys
            import os
            jarvis_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'JARVIS-MARK-5')
            if jarvis_path not in sys.path:
                sys.path.insert(0, jarvis_path)

            from jarvis_unified_voice import JARVISUnifiedVoice

            # Create JARVIS voice instance for STT/TTS
            self.jarvis_voice = JARVISUnifiedVoice()
            self.stt_engine = self.jarvis_voice
            self.tts_engine = self.jarvis_voice

            log_action(
                LogLevel.INFO,
                "voice_agent",
                "jarvis_voice_integrated"
            )

        except Exception as e:
            log_action(
                LogLevel.WARNING,
                "voice_agent",
                "jarvis_voice_unavailable",
                error=str(e)
            )
            # Fallback to basic implementations
            self._initialize_fallback_voice()

    def _initialize_fallback_voice(self):
        """Fallback voice components if JARVIS is unavailable."""
        try:
            import speech_recognition as sr
            self.stt_engine = sr.Recognizer()
        except ImportError:
            self.stt_engine = None

        try:
            import pyttsx3
            self.tts_engine = pyttsx3.init()
        except ImportError:
            self.tts_engine = None

    def _initialize_coqui_tts(self):
        """Initialize Coqui TTS for local, customizable voice synthesis."""
        if not TTS_AVAILABLE or not TORCH_AVAILABLE:
            log_action(
                LogLevel.WARNING,
                "voice_agent",
                "coqui_tts_unavailable",
                reason="TTS or torch not available"
            )
            return

        try:
            # Detect available device
            if torch.cuda.is_available():
                self.tts_device = "cuda"
                log_action(LogLevel.INFO, "voice_agent", "coqui_tts_cuda_available")
            else:
                self.tts_device = "cpu"
                log_action(LogLevel.INFO, "voice_agent", "coqui_tts_cpu_mode")

            # Initialize TTS with a good default model
            # Using a fast, high-quality model
            self.tts_model = TTS("tts_models/en/ljspeech/tacotron2-DDC_ph").to(self.tts_device)

            log_action(
                LogLevel.INFO,
                "voice_agent",
                "coqui_tts_initialized",
                device=self.tts_device
            )

        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "voice_agent",
                "coqui_tts_init_failed",
                error=str(e)
            )
            self.tts_model = None

    def set_voice_personality(
        self,
        personality: VoicePersonality,
        response_style: ResponseStyle = None,
        vocabulary: VocabularyLevel = None
    ):
        """Configure voice personality settings."""
        self.voice_personality = personality
        if response_style:
            self.response_style = response_style
        if vocabulary:
            self.vocabulary_level = vocabulary

        log_action(
            LogLevel.INFO,
            "voice_agent",
            "voice_personality_updated",
            personality=personality.value,
            style=self.response_style.value,
            vocabulary=self.vocabulary_level.value
        )

    def speak_with_coqui(self, text: str) -> bool:
        """Speak text using Coqui TTS with voice personality."""
        if not self.tts_model:
            log_action(LogLevel.WARNING, "voice_agent", "coqui_tts_not_available")
            return False

        try:
            # Apply voice personality modifications
            processed_text = self._apply_voice_personality(text)

            # Generate speech
            wav = self.tts_model.tts(text=processed_text)

            # Play audio using pygame if available
            if PYGAME_AVAILABLE:
                import pygame
                import io
                import numpy as np

                # Convert to pygame compatible format
                wav_np = np.array(wav)
                wav_bytes = (wav_np * 32767).astype(np.int16).tobytes()

                # Create pygame sound
                sound = pygame.mixer.Sound(buffer=wav_bytes)
                sound.play()

                # Wait for playback to finish
                while pygame.mixer.get_busy():
                    time.sleep(0.1)

                return True
            else:
                log_action(LogLevel.WARNING, "voice_agent", "pygame_not_available_for_audio")
                return False

        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "voice_agent",
                "coqui_tts_speak_failed",
                error=str(e)
            )
            return False

    def _apply_voice_personality(self, text: str) -> str:
        """Apply voice personality modifications to text."""
        # Adjust text based on personality settings
        if self.voice_personality == VoicePersonality.CALM:
            # Add calming phrases, slower delivery implied by text
            if self.response_style == ResponseStyle.SHORT:
                return text
            else:
                return f"I understand. {text}"

        elif self.voice_personality == VoicePersonality.DIRECT:
            # Remove fluff, be concise
            return text.replace("I understand. ", "").replace("Let me help you with that. ", "")

        elif self.voice_personality == VoicePersonality.SHARP:
            # Add emphasis, urgency
            return text.replace(".", "!").upper() if len(text) < 50 else text

        elif self.voice_personality == VoicePersonality.MENTOR:
            # Add guidance, wisdom
            if self.response_style == ResponseStyle.DETAILED:
                return f"Let me guide you through this. {text}"
            else:
                return f"Here's what you need to know: {text}"

        return text

    def store_voice_memory(self, key: str, data: Dict[str, Any]):
        """Store personalized voice memory for context awareness."""
        memory_item = {
            "key": key,
            "data": data,
            "timestamp": time.time(),
            "session_id": self.session_id
        }

        # Store in conversation history for short-term memory
        self.conversation_history.append(memory_item)

        # Keep only last 100 items
        if len(self.conversation_history) > 100:
            self.conversation_history = self.conversation_history[-100:]

        log_action(
            LogLevel.INFO,
            "voice_agent",
            "voice_memory_stored",
            key=key,
            data_keys=list(data.keys())
        )

    def retrieve_voice_memory(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieve personalized voice memory."""
        for item in reversed(self.conversation_history):
            if item["key"] == key:
                return item["data"]
        return None

    def get_voice_context(self) -> Dict[str, Any]:
        """Get current voice context including personality and memory."""
        return {
            "personality": self.voice_personality.value,
            "response_style": self.response_style.value,
            "vocabulary_level": self.vocabulary_level.value,
            "authenticated": self.authenticated,
            "session_id": self.session_id,
            "conversation_length": len(self.conversation_history),
            "last_interaction": self.conversation_history[-1] if self.conversation_history else None
        }

    def register_custom_command(self, command_phrase: str, action: Callable, description: str = ""):
        """Register a custom voice command."""
        if not hasattr(self, 'custom_commands'):
            self.custom_commands = {}

        self.custom_commands[command_phrase.lower()] = {
            "action": action,
            "description": description,
            "registered_at": time.time()
        }

        log_action(
            LogLevel.INFO,
            "voice_agent",
            "custom_command_registered",
            command=command_phrase,
            description=description
        )

    def process_custom_command(self, command: str) -> Optional[Any]:
        """Process a custom voice command."""
        if not hasattr(self, 'custom_commands'):
            return None

        command_lower = command.lower().strip()

        # Check for exact matches first
        if command_lower in self.custom_commands:
            action_info = self.custom_commands[command_lower]
            try:
                result = action_info["action"]()
                log_action(
                    LogLevel.INFO,
                    "voice_agent",
                    "custom_command_executed",
                    command=command_lower
                )
                return result
            except Exception as e:
                log_action(
                    LogLevel.ERROR,
                    "voice_agent",
                    "custom_command_failed",
                    command=command_lower,
                    error=str(e)
                )
                return None

        # Check for partial matches
        for registered_command, action_info in self.custom_commands.items():
            if registered_command in command_lower:
                try:
                    result = action_info["action"]()
                    log_action(
                        LogLevel.INFO,
                        "voice_agent",
                        "custom_command_executed_partial",
                        command=command_lower,
                        matched=registered_command
                    )
                    return result
                except Exception as e:
                    log_action(
                        LogLevel.ERROR,
                        "voice_agent",
                        "custom_command_failed",
                        command=command_lower,
                        error=str(e)
                    )
                    return None

        return None

    def process(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process voice commands and route through orchestrator.

        Args:
            user_input: Transcribed voice command
            context: Voice processing context (confidence, audio metadata)

        Returns:
            Response dict with voice-specific handling
        """
        timestamp_ms = int(time.time() * 1000)
        trace = ReasoningTrace(
            agent_name=self.name,
            user_input=user_input,
            timestamp=timestamp_ms,
        )

        try:
            # Step 1: Authenticate and validate voice command
            auth_result = self._authenticate_voice_command(user_input)
            if not auth_result["authenticated"]:
                return self._create_error_response("Voice authentication failed", trace)

            step_1 = ReasoningStep(
                step_id="voice_auth",
                action="authenticate_voice_command",
                input=user_input,
                output=auth_result,
                confidence=auth_result.get("confidence", 0.5),
            )
            trace.steps.append(step_1)

            # Step 2: Classify intent and safety level
            intent_analysis = self._classify_voice_intent(user_input)
            step_2 = ReasoningStep(
                step_id="intent_classification",
                action="classify_voice_intent",
                input=user_input,
                output=intent_analysis,
                confidence=intent_analysis.get("confidence", 0.7),
            )
            trace.steps.append(step_2)

            # Step 3: Check safety and get confirmation if needed
            safety_check = self._check_command_safety(intent_analysis)
            if safety_check["level"] == VoiceSafetyLevel.CONFIRMED:
                if not self._get_user_confirmation(user_input):
                    return self._create_confirmation_denied_response(trace)

            step_3 = ReasoningStep(
                step_id="safety_check",
                action="check_command_safety",
                input=intent_analysis,
                output=safety_check,
                confidence=0.9,
            )
            trace.steps.append(step_3)

            # Step 4: Execute system command or route to orchestrator
            if intent_analysis["is_system_command"]:
                result = self._execute_system_command(intent_analysis)
            else:
                # Route through orchestrator for agent processing
                result = self._route_through_orchestrator(user_input, context)

            step_4 = ReasoningStep(
                step_id="command_execution",
                action="execute_system_command" if intent_analysis["is_system_command"] else "route_to_orchestrator",
                input=intent_analysis,
                output=result,
                confidence=result.get("confidence", 0.8),
            )
            trace.steps.append(step_4)

            # Step 5: Generate voice response
            response_text = result.get("response", "Command executed successfully")
            voice_response = self._generate_voice_response(response_text, result)

            # Update conversation history
            self._update_voice_memory(user_input, response_text, result)

            # Calculate final confidence
            overall_confidence = self._calculate_final_confidence(trace.steps)
            trace.final_confidence = overall_confidence
            trace.final_decision = response_text
            active_session_id = (context or {}).get("session_id") or self.session_id

            # Emit trace event
            trace_event = Event.new(
                source="voice_agent",
                type="reasoning_trace",
                payload={
                    "task_type": "voice_interaction",
                    "language": "spoken_english",
                    "issues_detected": [],
                    "recommendations": [],
                    "confidence": overall_confidence,
                    "execution_time_ms": int(time.time() * 1000) - timestamp_ms,
                    "analysis_mode": "voice",
                    "clarification_required": False,
                    "voice_mode": self.voice_mode.value,
                    "authenticated": auth_result["authenticated"],
                    "safety_level": safety_check["level"].value,
                },
                confidence=overall_confidence,
                session_id=active_session_id,
                correlation_id=context.get("correlation_id") if context else None,
            )
            self.event_store.add_event(trace_event)

            log_action(
                LogLevel.INFO,
                "voice_agent",
                "voice_command_processed",
                command_type=intent_analysis.get("command_type"),
                safety_level=safety_check["level"].value,
                confidence=overall_confidence
            )

            return {
                "response": response_text,
                "voice_response": voice_response,
                "task_type": "voice_interaction",
                "language": "spoken_english",
                "analysis": intent_analysis,
                "confidence": overall_confidence,
                "safety_level": safety_check["level"].value,
                "authenticated": auth_result["authenticated"],
                "trace_event_id": trace_event.id,
                "agent": "voice",
            }

        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "voice_agent",
                "processing_error",
                error=str(e)
            )
            return {
                "response": f"Voice processing error: {str(e)}",
                "confidence": 0.0,
                "trace_event_id": None,
                "agent": "voice",
            }

    def _authenticate_voice_command(self, user_input: str) -> Dict[str, Any]:
        """Authenticate voice command based on wake words and timing."""
        current_time = time.time()

        # Check for wake word in recent input
        has_wake_word = any(wake_word in user_input.lower() for wake_word in self.WAKE_WORDS)

        if has_wake_word:
            self.authenticated = True
            self.last_wake_word_time = current_time
            return {
                "authenticated": True,
                "method": "wake_word",
                "confidence": 0.9,
                "timestamp": current_time
            }

        # Check if within wake word timeout
        if self.authenticated and (current_time - self.last_wake_word_time) < self.wake_word_timeout:
            return {
                "authenticated": True,
                "method": "timeout_window",
                "confidence": 0.7,
                "timestamp": current_time
            }

        return {
            "authenticated": False,
            "method": "none",
            "confidence": 0.0,
            "reason": "no_recent_wake_word"
        }

    def _classify_voice_intent(self, user_input: str) -> Dict[str, Any]:
        """Classify voice command intent and extract parameters."""
        input_lower = user_input.lower()

        # Check for system commands - prioritize more specific matches
        command_matches = []

        # Check safe commands
        for command, keywords in self.SAFE_COMMANDS.items():
            matches = [keyword for keyword in keywords if keyword in input_lower]
            if matches:
                # Calculate specificity score (more unique keywords = higher score)
                specificity = len(matches) / len(keywords)
                command_matches.append({
                    "command": command,
                    "matches": matches,
                    "specificity": specificity,
                    "safety_level": VoiceSafetyLevel.SAFE
                })

        # Check confirmed commands
        for command, keywords in self.CONFIRMED_COMMANDS.items():
            matches = [keyword for keyword in keywords if keyword in input_lower]
            if matches:
                specificity = len(matches) / len(keywords)
                command_matches.append({
                    "command": command,
                    "matches": matches,
                    "specificity": specificity,
                    "safety_level": VoiceSafetyLevel.CONFIRMED
                })

        # Check action templates
        for action_name, template in self.ACTION_TEMPLATES.items():
            # Look for action keywords in input
            action_keywords = template.get("keywords", [action_name.replace("_", " ")])
            matches = [keyword for keyword in action_keywords if keyword in input_lower]
            if matches:
                specificity = len(matches) / len(action_keywords)
                extracted_params = self._extract_action_parameters(input_lower, template)
                command_matches.append({
                    "command": action_name,
                    "matches": matches,
                    "specificity": specificity,
                    "safety_level": VoiceSafetyLevel.SAFE,
                    "action_template": template,
                    "parameters": extracted_params
                })

        if command_matches:
            # Sort by specificity (highest first), then by number of matches
            command_matches.sort(key=lambda x: (x["specificity"], len(x["matches"])), reverse=True)
            best_match = command_matches[0]

            return {
                "is_system_command": True,
                "command_type": best_match["command"],
                "safety_level": best_match["safety_level"],
                "confidence": 0.8,
                "parameters": best_match.get("parameters", {}),
                "action_template": best_match.get("action_template"),
                "original_text": user_input
            }

        # Not a system command - route to orchestrator
        return {
            "is_system_command": False,
            "command_type": "orchestrator_routing",
            "safety_level": VoiceSafetyLevel.SAFE,
            "confidence": 0.6,
            "parameters": {}
        }

    def _extract_command_parameters(self, input_text: str, keywords: List[str]) -> Dict[str, Any]:
        """Extract parameters from voice command."""
        # Simple parameter extraction - can be enhanced
        params = {}
        input_lower = input_text.lower()

        # Look for application names with better specificity
        if any(word in input_lower for word in ["vscode", "code", "editor", "ide"]):
            params["application"] = "vscode"
        elif any(word in input_lower for word in ["browser", "chrome", "firefox", "web", "internet"]):
            if "firefox" in input_lower:
                params["application"] = "firefox"
            else:
                params["application"] = "chrome"
        elif any(word in input_lower for word in ["terminal", "cmd", "command", "shell", "console"]):
            params["application"] = "terminal"

        return params

    def _extract_action_parameters(self, input_text: str, template: Dict[str, Any]) -> Dict[str, Any]:
        """Extract parameters for action templates."""
        params = {}
        words = input_text.split()

        # Extract based on template parameter types
        template_params = template.get("parameters", [])

        for param in template_params:
            if param == "project_path":
                # Look for directory-like strings
                for word in words:
                    if "/" in word or "\\" in word or word in ["current", "here", "this"]:
                        params[param] = word
                        break
            elif param == "file_path":
                # Look for file extensions
                for word in words:
                    if "." in word and len(word.split(".")[-1]) <= 4:  # reasonable extension length
                        params[param] = word
                        break
            elif param == "url":
                # Look for URLs
                for word in words:
                    if word.startswith(('http', 'www')):
                        params[param] = word
                        break
            elif param == "filename":
                # Look for filenames
                for word in words:
                    if "." in word:
                        params[param] = word
                        break
            elif param == "command":
                # For terminal commands, take everything after certain keywords
                command_keywords = ["run", "execute", "do"]
                for i, word in enumerate(words):
                    if word in command_keywords and i + 1 < len(words):
                        params[param] = " ".join(words[i + 1:])
                        break

        return params

    def _check_command_safety(self, intent_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Check safety level of voice command."""
        safety_level = intent_analysis.get("safety_level", VoiceSafetyLevel.RESTRICTED)

        return {
            "level": safety_level,
            "allowed": safety_level in [VoiceSafetyLevel.SAFE, VoiceSafetyLevel.CONFIRMED],
            "requires_confirmation": safety_level == VoiceSafetyLevel.CONFIRMED,
            "reason": f"Command classified as {safety_level.value}"
        }

    def _get_user_confirmation(self, command: str) -> bool:
        """Get user confirmation for dangerous commands."""
        # In a real implementation, this would use voice input
        # For now, return True (simulating confirmation)
        log_action(
            LogLevel.WARNING,
            "voice_agent",
            "confirmation_required",
            command=command,
            confirmation_action="auto_approved_for_demo"
        )
        return True  # Auto-approve for demo purposes

    def _execute_system_command(self, intent_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Execute safe system command."""
        command_type = intent_analysis["command_type"]
        parameters = intent_analysis.get("parameters", {})

        try:
            # Check if this is an action template
            if command_type in self.ACTION_TEMPLATES:
                template = self.ACTION_TEMPLATES[command_type]
                action_method = getattr(self, template["implementation"], None)
                if action_method:
                    # Extract parameters for the action template
                    action_params = self._extract_action_parameters(
                        intent_analysis.get("original_text", ""),
                        template
                    )
                    result = action_method(**action_params)
                else:
                    result = {"success": False, "error": f"Action method {template['implementation']} not found"}
            elif command_type == "open_vscode":
                result = self._open_vscode()
            elif command_type == "open_browser":
                result = self._open_browser()
            elif command_type == "open_terminal":
                result = self._open_terminal()
            elif command_type == "take_screenshot":
                result = self._take_screenshot()
            elif command_type == "system_status":
                result = self._get_system_status()
            elif command_type == "shutdown_system":
                result = self._shutdown_system()
            elif command_type == "restart_system":
                result = self._restart_system()
            else:
                result = {"success": False, "error": f"Unknown command: {command_type}"}

            # Notify callback if set
            if self.on_system_action:
                self.on_system_action(command_type, result)

            return result

        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "voice_agent",
                "system_command_failed",
                command=command_type,
                error=str(e)
            )
            return {
                "success": False,
                "error": str(e),
                "response": f"Failed to execute {command_type}: {str(e)}",
                "confidence": 0.0
            }

    def _route_through_orchestrator(self, user_input: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Route non-system commands through the orchestrator."""
        # Import here to avoid circular imports
        from blackfong_ecosystem.unified_orchestrator import unified_orchestrator

        # Add voice context
        voice_context = context or {}
        voice_context.update({
            "voice_input": True,
            "voice_mode": self.voice_mode.value,
            "authenticated": self.authenticated,
        })

        return unified_orchestrator.process_input(
            user_input,
            modality=unified_orchestrator.InputModality.TEXT,  # Treat as text after STT
            context=voice_context
        )

    def _generate_voice_response(self, text_response: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Generate voice response using TTS."""
        try:
            if self.tts_engine and hasattr(self.tts_engine, 'speak'):
                # Use JARVIS TTS
                self.tts_engine.speak(text_response)
                return {"success": True, "method": "jarvis_tts"}
            elif self.tts_engine:
                # Fallback TTS
                self.tts_engine.say(text_response)
                self.tts_engine.runAndWait()
                return {"success": True, "method": "fallback_tts"}
            else:
                return {"success": False, "error": "No TTS engine available"}
        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "voice_agent",
                "tts_failed",
                error=str(e)
            )
            return {"success": False, "error": str(e)}

    def _update_voice_memory(self, user_input: str, response: str, result: Dict[str, Any]):
        """Update voice conversation memory."""
        self.conversation_history.append({
            "timestamp": time.time(),
            "user_input": user_input,
            "response": response,
            "result": result,
            "authenticated": self.authenticated,
        })

        # Keep only recent history (last 50 interactions)
        if len(self.conversation_history) > 50:
            self.conversation_history = self.conversation_history[-50:]

    def _calculate_final_confidence(self, steps: List[ReasoningStep]) -> float:
        """Calculate overall confidence from reasoning steps."""
        if not steps:
            return 0.0
        return round(sum(step.confidence for step in steps) / len(steps), 2)

    def _create_error_response(self, message: str, trace: ReasoningTrace) -> Dict[str, Any]:
        """Create error response for failed authentication."""
        return {
            "response": message,
            "confidence": 0.0,
            "authenticated": False,
            "trace_event_id": None,
            "agent": "voice",
        }

    def _create_confirmation_denied_response(self, trace: ReasoningTrace) -> Dict[str, Any]:
        """Create response when user denies confirmation."""
        return {
            "response": "Command cancelled - confirmation denied",
            "confidence": 0.5,
            "safety_level": VoiceSafetyLevel.CONFIRMED.value,
            "trace_event_id": None,
            "agent": "voice",
        }

    # System command implementations (safe operations only)

    def _open_vscode(self) -> Dict[str, Any]:
        """Open VS Code editor."""
        try:
            import subprocess
            import platform

            if platform.system() == "Windows":
                subprocess.Popen(["code"], shell=True)
            elif platform.system() == "Darwin":  # macOS
                subprocess.Popen(["open", "-a", "Visual Studio Code"])
            else:  # Linux
                subprocess.Popen(["code"])

            return {
                "success": True,
                "response": "Opening VS Code",
                "confidence": 0.9
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": "Failed to open VS Code",
                "confidence": 0.0
            }

    def _open_browser(self) -> Dict[str, Any]:
        """Open web browser."""
        try:
            import webbrowser
            webbrowser.open("https://www.google.com")
            return {
                "success": True,
                "response": "Opening web browser",
                "confidence": 0.9
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": "Failed to open browser",
                "confidence": 0.0
            }

    def _open_terminal(self) -> Dict[str, Any]:
        """Open terminal/command prompt."""
        try:
            import subprocess
            import platform

            if platform.system() == "Windows":
                subprocess.Popen(["cmd"], shell=True)
            elif platform.system() == "Darwin":  # macOS
                subprocess.Popen(["open", "-a", "Terminal"])
            else:  # Linux
                subprocess.Popen(["x-terminal-emulator"])

            return {
                "success": True,
                "response": "Opening terminal",
                "confidence": 0.9
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": "Failed to open terminal",
                "confidence": 0.0
            }

    def _take_screenshot(self) -> Dict[str, Any]:
        """Take a screenshot."""
        try:
            import pyautogui
            screenshot = pyautogui.screenshot()
            timestamp = int(time.time())
            filename = f"screenshot_{timestamp}.png"
            screenshot.save(filename)
            return {
                "success": True,
                "response": f"Screenshot saved as {filename}",
                "confidence": 0.9,
                "filename": filename
            }
        except ImportError:
            return {
                "success": False,
                "error": "pyautogui not available",
                "response": "Screenshot functionality not available",
                "confidence": 0.0
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": "Failed to take screenshot",
                "confidence": 0.0
            }

    def _get_system_status(self) -> Dict[str, Any]:
        """Get basic system status."""
        try:
            import platform
            import psutil

            status = {
                "platform": platform.system(),
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_usage": psutil.disk_usage('/').percent,
            }

            response = f"System status: CPU {status['cpu_percent']}%, Memory {status['memory_percent']}%, Disk {status['disk_usage']}%"

            return {
                "success": True,
                "response": response,
                "confidence": 0.9,
                "status": status
            }
        except ImportError:
            return {
                "success": False,
                "error": "psutil not available",
                "response": "System monitoring not available",
                "confidence": 0.0
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": "Failed to get system status",
                "confidence": 0.0
            }

    # Action Template Implementations

    def _open_code_workspace(self, project_path: str = None, editor: str = None) -> Dict[str, Any]:
        """Open a code workspace with specified editor."""
        try:
            editor = editor or self.ACTION_TEMPLATES["open_code_workspace"]["default_editor"]

            if not project_path:
                # Try to infer from desktop context or current directory
                project_path = self._infer_project_path()

            if not project_path:
                return {
                    "success": False,
                    "error": "No project path specified or detected",
                    "response": "Please specify which project to open",
                    "confidence": 0.0
                }

            if editor.lower() == "vscode":
                import subprocess
                import platform

                if platform.system() == "Windows":
                    subprocess.Popen(["code", project_path], shell=True)
                else:
                    subprocess.Popen(["code", project_path])

                return {
                    "success": True,
                    "response": f"Opening {project_path} in VS Code",
                    "confidence": 0.9,
                    "action": "open_code_workspace",
                    "project_path": project_path,
                    "editor": editor
                }
            else:
                return {
                    "success": False,
                    "error": f"Unsupported editor: {editor}",
                    "response": f"Editor {editor} is not supported",
                    "confidence": 0.0
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": f"Failed to open workspace: {str(e)}",
                "confidence": 0.0
            }

    def _run_debug_session(self, file_path: str = None, debugger_type: str = None) -> Dict[str, Any]:
        """Start a debugging session for a file."""
        try:
            debugger_type = debugger_type or self.ACTION_TEMPLATES["run_debug_session"]["default_debugger"]

            if not file_path:
                # Try to infer from desktop context
                file_path = self._infer_active_file()

            if not file_path:
                return {
                    "success": False,
                    "error": "No file specified or detected",
                    "response": "Please specify which file to debug",
                    "confidence": 0.0
                }

            # For now, just open the file in VS Code with debug
            # In production, this would integrate with actual debuggers
            import subprocess
            import platform

            if platform.system() == "Windows":
                subprocess.Popen(["code", "--goto", file_path], shell=True)
            else:
                subprocess.Popen(["code", "--goto", file_path])

            return {
                "success": True,
                "response": f"Opening {file_path} for debugging",
                "confidence": 0.8,
                "action": "run_debug_session",
                "file_path": file_path,
                "debugger_type": debugger_type
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": f"Failed to start debug session: {str(e)}",
                "confidence": 0.0
            }

    def _launch_browser_with_url(self, url: str = None, browser: str = None) -> Dict[str, Any]:
        """Launch browser with specific URL."""
        try:
            browser = browser or self.ACTION_TEMPLATES["launch_browser_with_url"]["default_browser"]

            if not url:
                return {
                    "success": False,
                    "error": "No URL specified",
                    "response": "Please specify a URL to open",
                    "confidence": 0.0
                }

            # Ensure URL has protocol
            if not url.startswith(('http://', 'https://')):
                url = f"https://{url}"

            import webbrowser

            if browser == "default":
                webbrowser.open(url)
            else:
                # Try to use specific browser
                browser_path = self._get_browser_path(browser)
                if browser_path:
                    webbrowser.get(browser_path).open(url)
                else:
                    webbrowser.open(url)

            return {
                "success": True,
                "response": f"Opening {url} in browser",
                "confidence": 0.9,
                "action": "launch_browser_with_url",
                "url": url,
                "browser": browser
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": f"Failed to open browser: {str(e)}",
                "confidence": 0.0
            }

    def _search_and_open_file(self, filename: str = None, directory: str = None) -> Dict[str, Any]:
        """Search for and open a file."""
        try:
            if not filename:
                return {
                    "success": False,
                    "error": "No filename specified",
                    "response": "Please specify a filename to search for",
                    "confidence": 0.0
                }

            directory = directory or self._infer_project_path() or "."

            # Search for the file
            import os
            import glob

            # Try exact match first
            search_pattern = os.path.join(directory, filename)
            if os.path.exists(search_pattern):
                found_files = [search_pattern]
            else:
                # Try glob patterns
                found_files = glob.glob(os.path.join(directory, f"**/{filename}"), recursive=True)

            if not found_files:
                return {
                    "success": False,
                    "error": "File not found",
                    "response": f"Could not find {filename} in {directory}",
                    "confidence": 0.0
                }

            # Open the first match
            file_path = found_files[0]
            import subprocess
            import platform

            if platform.system() == "Windows":
                subprocess.Popen(["start", file_path], shell=True)
            elif platform.system() == "Darwin":  # macOS
                subprocess.Popen(["open", file_path])
            else:  # Linux
                subprocess.Popen(["xdg-open", file_path])

            return {
                "success": True,
                "response": f"Opening {os.path.basename(file_path)}",
                "confidence": 0.8,
                "action": "search_and_open_file",
                "file_path": file_path,
                "searched_filename": filename
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": f"Failed to search and open file: {str(e)}",
                "confidence": 0.0
            }

    def _run_terminal_command(self, command: str = None, working_directory: str = None) -> Dict[str, Any]:
        """Execute a safe terminal command."""
        try:
            if not command:
                return {
                    "success": False,
                    "error": "No command specified",
                    "response": "Please specify a command to run",
                    "confidence": 0.0
                }

            # Check if command is in safe list
            safe_commands = self.ACTION_TEMPLATES["run_terminal_command"]["safe_commands"]
            command_lower = command.lower().strip()

            is_safe = any(safe_cmd in command_lower for safe_cmd in safe_commands)

            if not is_safe:
                return {
                    "success": False,
                    "error": "Command not in safe list",
                    "response": f"Command '{command}' is not allowed for safety reasons",
                    "confidence": 0.0
                }

            import subprocess
            working_directory = working_directory or self._infer_project_path() or "."

            result = subprocess.run(
                command,
                shell=True,
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=30
            )

            response = f"Command executed: {command}"
            if result.stdout:
                response += f"\nOutput: {result.stdout[:200]}..."  # Limit output length

            return {
                "success": result.returncode == 0,
                "response": response,
                "confidence": 0.8,
                "action": "run_terminal_command",
                "command": command,
                "return_code": result.returncode,
                "output": result.stdout,
                "error": result.stderr
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Command timed out",
                "response": f"Command '{command}' timed out after 30 seconds",
                "confidence": 0.0
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "response": f"Failed to execute command: {str(e)}",
                "confidence": 0.0
            }

    def _create_new_project(self, project_type: str = None, project_name: str = None, location: str = None) -> Dict[str, Any]:
        """Create a new project with specified template."""
        try:
            if not project_name:
                return {
                    "success": False,
                    "error": "Project name required",
                    "response": "Please specify a project name",
                    "confidence": 0.0
                }

            # Default location if not specified
            if not location:
                location = os.getcwd()

            project_path = os.path.join(location, project_name)

            # Create project directory
            os.makedirs(project_path, exist_ok=True)

            # Basic project structure based on type
            if project_type == "python":
                # Create basic Python project structure
                with open(os.path.join(project_path, "requirements.txt"), "w") as f:
                    f.write("# Add your dependencies here\n")
                with open(os.path.join(project_path, "main.py"), "w") as f:
                    f.write('def main():\n    print("Hello, World!")\n\nif __name__ == "__main__":\n    main()\n')
                with open(os.path.join(project_path, "README.md"), "w") as f:
                    f.write(f"# {project_name}\n\nA Python project.\n")

            elif project_type == "web":
                # Create basic web project structure
                os.makedirs(os.path.join(project_path, "css"), exist_ok=True)
                os.makedirs(os.path.join(project_path, "js"), exist_ok=True)
                with open(os.path.join(project_path, "index.html"), "w") as f:
                    f.write(f'<!DOCTYPE html>\n<html>\n<head>\n    <title>{project_name}</title>\n</head>\n<body>\n    <h1>{project_name}</h1>\n</body>\n</html>\n')
                with open(os.path.join(project_path, "css", "style.css"), "w") as f:
                    f.write("/* Add your styles here */\n")
                with open(os.path.join(project_path, "js", "script.js"), "w") as f:
                    f.write("// Add your JavaScript here\n")

            else:
                # Generic project
                with open(os.path.join(project_path, "README.md"), "w") as f:
                    f.write(f"# {project_name}\n\nA new project.\n")

            log_action(
                LogLevel.INFO,
                "voice_agent",
                "project_created",
                project_name=project_name,
                project_type=project_type,
                location=project_path
            )

            return {
                "success": True,
                "response": f"Created new {project_type or 'generic'} project '{project_name}' at {project_path}",
                "project_path": project_path,
                "confidence": 0.9
            }

        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "voice_agent",
                "project_creation_failed",
                project_name=project_name,
                error=str(e)
            )
            return {
                "success": False,
                "error": str(e),
                "response": f"Failed to create project: {str(e)}",
                "confidence": 0.0
            }

    # Context Inference Helpers

    def _infer_project_path(self) -> str:
        """Infer the current project path from context."""
        # Check desktop context first
        if hasattr(self, 'desktop_context') and self.desktop_context:
            # This would be set by the voice loop controller
            active_window = self.desktop_context.get('active_window_title', '')
            if 'vscode' in active_window.lower():
                # Try to get the workspace from VS Code title
                # This is a simplified implementation
                pass

        # Fallback to current working directory
        import os
        cwd = os.getcwd()
        if os.path.exists(os.path.join(cwd, '.git')) or os.path.exists(os.path.join(cwd, 'requirements.txt')):
            return cwd

        return None

    def _infer_active_file(self) -> str:
        """Infer the currently active file from context."""
        # Check desktop context
        if hasattr(self, 'desktop_context') and self.desktop_context:
            active_window = self.desktop_context.get('active_window_title', '')
            # Try to extract file path from window title
            # This is highly dependent on the application
            if ' - ' in active_window:
                # Common pattern: "filename.ext - Application"
                parts = active_window.split(' - ')
                if len(parts) >= 2:
                    potential_filename = parts[0].strip()
                    if '.' in potential_filename:
                        return potential_filename

        return None

    def _get_browser_path(self, browser: str) -> str:
        """Get browser executable path."""
        import platform
        system = platform.system()

        browser_paths = {
            "chrome": {
                "Windows": "C:/Program Files/Google/Chrome/Application/chrome.exe",
                "Darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "Linux": "/usr/bin/google-chrome"
            },
            "firefox": {
                "Windows": "C:/Program Files/Mozilla Firefox/firefox.exe",
                "Darwin": "/Applications/Firefox.app/Contents/MacOS/firefox",
                "Linux": "/usr/bin/firefox"
            }
        }

        if browser in browser_paths and system in browser_paths[browser]:
            import os
            path = browser_paths[browser][system]
            if os.path.exists(path):
                return path

        return None

    def _shutdown_system(self) -> Dict[str, Any]:
        """Shutdown system (requires confirmation)."""
        # This is a dangerous operation - in real implementation would require
        # explicit confirmation and possibly admin privileges
        return {
            "success": False,
            "error": "Shutdown not implemented for safety",
            "response": "System shutdown cancelled for safety reasons",
            "confidence": 0.0
        }

    def _restart_system(self) -> Dict[str, Any]:
        """Restart system (requires confirmation)."""
        # This is a dangerous operation - in real implementation would require
        # explicit confirmation and possibly admin privileges
        return {
            "success": False,
            "error": "Restart not implemented for safety",
            "response": "System restart cancelled for safety reasons",
            "confidence": 0.0
        }

    # Voice processing methods

    def start_listening(self):
        """Start voice listening mode."""
        if self.is_listening:
            return

        self.is_listening = True

        # Start processing thread
        self.processing_thread = threading.Thread(target=self._voice_processing_loop, daemon=True)
        self.processing_thread.start()

        log_action(
            LogLevel.INFO,
            "voice_agent",
            "listening_started",
            mode=self.voice_mode.value
        )

    def stop_listening(self):
        """Stop voice listening mode."""
        self.is_listening = False

        log_action(
            LogLevel.INFO,
            "voice_agent",
            "listening_stopped"
        )

    def _voice_processing_loop(self):
        """Main voice processing loop."""
        while self.is_listening:
            try:
                if self.voice_mode == VoiceMode.PUSH_TO_TALK:
                    # Wait for push-to-talk trigger
                    self._wait_for_push_to_talk()
                elif self.voice_mode == VoiceMode.WAKE_WORD:
                    # Continuous listening for wake word
                    self._listen_for_wake_word()
                elif self.voice_mode == VoiceMode.ALWAYS_LISTEN:
                    # Always listening (not recommended)
                    self._process_continuous_audio()

                # Process audio and get transcription
                transcription = self._capture_and_transcribe_audio()
                if transcription:
                    # Process the transcribed command
                    result = self.process(transcription)

                    # Handle response
                    if result.get("voice_response", {}).get("success"):
                        # TTS already handled in process()
                        pass
                    else:
                        # Fallback text response
                        print(f"Voice Agent: {result.get('response', 'Command processed')}")

            except Exception as e:
                log_action(
                    LogLevel.ERROR,
                    "voice_agent",
                    "voice_processing_error",
                    error=str(e)
                )
                time.sleep(1)  # Brief pause before retry

    def _wait_for_push_to_talk(self):
        """Wait for push-to-talk trigger."""
        # In a real implementation, this would monitor keyboard/mouse input
        # For now, just sleep briefly
        time.sleep(0.1)

    def _listen_for_wake_word(self):
        """Listen for wake word activation."""
        # In a real implementation, this would continuously monitor audio
        # For now, just sleep briefly
        time.sleep(0.1)

    def _process_continuous_audio(self):
        """Process continuous audio input."""
        # Not recommended for safety reasons
        time.sleep(0.1)

    def _capture_and_transcribe_audio(self) -> Optional[str]:
        """Capture audio and convert to text."""
        try:
            if self.stt_engine and hasattr(self.stt_engine, 'listen_and_transcribe'):
                # Use JARVIS STT
                return self.stt_engine.listen_and_transcribe()
            elif self.stt_engine:
                # Fallback STT using speech_recognition
                with self.stt_engine as source:
                    audio = self.stt_engine.listen(source)
                    return self.stt_engine.recognize_google(audio)
            else:
                return None
        except Exception as e:
            log_action(
                LogLevel.ERROR,
                "voice_agent",
                "transcription_failed",
                error=str(e)
            )
            return None

    def get_voice_history(self) -> List[Dict[str, Any]]:
        """Get recent voice interaction history."""
        return self.conversation_history[-10:]  # Last 10 interactions

    def clear_voice_memory(self):
        """Clear voice conversation memory."""
        self.conversation_history.clear()
        log_action(
            LogLevel.INFO,
            "voice_agent",
            "voice_memory_cleared"
        )
