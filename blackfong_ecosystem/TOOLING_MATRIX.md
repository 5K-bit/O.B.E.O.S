# Tool-Calling Coordination Matrix

This document defines which agents can invoke which tools from the O.B.E.O.S ServiceRegistry.

## Overview

The Blackfong system uses a capability-aware tool routing system. Each agent declares its capabilities, and the ToolDispatcher routes requests intelligently.

```
Agent → invoke_tool() → ToolDispatcher → ServiceRegistry → Service
```

## Capability Matrix

### Agent: JARVIS (Reasoning Agent)

**Primary Role:** General reasoning, orchestration, user-facing interface

| Tool | Service | Action | Use Case | Enabled |
|------|---------|--------|----------|---------|
| `transcriber` | transcriber | transcribe | Convert audio to text | ✅ YES |
| `transcriber` | transcriber | get_transcript | Retrieve past transcriptions | ✅ YES |
| `vision_detector` | vision_detector | detect_objects | Analyze images | ✅ YES |
| `quiz` | lilo | get_question | Request next quiz question | ✅ YES |
| `quiz` | lilo | submit_answer | Grade user answer | ✅ YES |
| `exercise` | stitch | get_exercise | Request programming exercise | ✅ YES |
| `exercise` | stitch | submit_code | Grade code submission | ✅ YES |
| `memory` | blackfong_control | store_memory | Store reasoning result | ✅ YES |
| `memory` | blackfong_control | retrieve_memory | Query past reasoning | ✅ YES |

**Rationale:**
- JARVIS is the top-level reasoning agent
- Can invoke all training agents (lilo, stitch) as tools
- Can access all sensors (transcriber, vision)
- Can store reasoning traces in memory

---

### Agent: Lilo (Certification Training)

**Primary Role:** Certification exam prep, quiz generation, progress tracking

| Tool | Service | Action | Use Case | Enabled |
|------|---------|--------|----------|---------|
| `transcriber` | transcriber | transcribe | Transcribe user spoken answers | ❌ NO |
| `vision_detector` | vision_detector | detect_objects | Analyze exam content images | ✅ YES |
| `quiz` | lilo | get_question | Get next question (self-invoke) | ❌ NO |
| `exercise` | stitch | get_exercise | Cross-reference Python concepts | ✅ YES |
| `memory` | blackfong_control | store_memory | Persist quiz progress | ✅ YES |
| `memory` | blackfong_control | retrieve_memory | Load quiz history | ✅ YES |

**Rationale:**
- Lilo is a specialist training agent
- Can cross-reference Python exercises (stitch) for related topics
- Should NOT self-invoke quiz generation (prevents recursion)
- Audio transcription is JARVIS's responsibility
- Can access vision for image-based exam content

---

### Agent: Stitch (Python Learning)

**Primary Role:** Python language learning, code validation, exercise progression

| Tool | Service | Action | Use Case | Enabled |
|------|---------|--------|----------|---------|
| `transcriber` | transcriber | transcribe | Transcribe spoken questions | ❌ NO |
| `vision_detector` | vision_detector | detect_objects | Detect objects in code samples | ✅ MAYBE |
| `quiz` | lilo | get_question | Cross-reference security concepts | ✅ YES |
| `exercise` | stitch | get_exercise | Get next exercise (self-invoke) | ❌ NO |
| `memory` | blackfong_control | store_memory | Persist progress | ✅ YES |
| `memory` | blackfong_control | retrieve_memory | Load learning history | ✅ YES |

**Rationale:**
- Stitch is a specialist learning agent
- Can cross-reference certification topics (lilo) for context
- Should NOT self-invoke exercises (prevents recursion)
- Audio transcription is JARVIS's responsibility
- Maybe vision for analyzing structure in code samples (limited)

---

## Service Registry Reference

```
Service: transcriber
  Capabilities:
    - action: "transcribe" (audio → text)
    - action: "get_transcript" (retrieve transcript)
  
Service: vision_detector
  Capabilities:
    - action: "detect_objects" (image → objects)
    - action: "classify" (image → classes)

Service: lilo
  Capabilities:
    - action: "get_question" (request quiz question)
    - action: "submit_answer" (grade answer)
    - action: "get_progress" (fetch stats)

Service: stitch
  Capabilities:
    - action: "get_exercise" (next exercise)
    - action: "submit_code" (grade code)
    - action: "get_hint" (provide hint)

Service: blackfong_control (O.B.E.O.S Memory)
  Capabilities:
    - action: "store_memory" (persist data)
    - action: "retrieve_memory" (query data)
    - action: "query_events" (search events)
```

---

## Tool Invocation Constraints

### Rule 1: No Self-Invocation
Agents MUST NOT invoke their own tools directly (prevents infinite loops).

```python
# ❌ BLOCKED
if agent.name == tool_request.tool_name:
    return error("Self-invocation not permitted")

# ✅ ALLOWED
if agent.name == "jarvis" and tool_request.tool_name == "lilo":
    return dispatch_to_lilo()
```

### Rule 2: Capability Verification
The ToolDispatcher verifies that the requested service and action exist in ServiceRegistry.

```python
service = registry.get_service(tool_name)
capability = service.get_capability(action)
if not capability:
    return error("Capability not found")
```

### Rule 3: Agent Hierarchy (Optional)
- **Hierarchy Level 1 (Orchestration):** JARVIS - can invoke all services
- **Hierarchy Level 2 (Specialization):** Lilo, Stitch - can invoke other specialists + memory
- **Hierarchy Level 3 (Foundation):** Services (transcriber, vision, memory) - invoked by agents, don't invoke others

### Rule 4: Mutual Non-Invocation
Lilo and Stitch should NOT invoke each other directly except through JARVIS orchestration.

```python
# ❌ Discouraged (creates coupling)
lilo.invoke_tool("stitch", "get_exercise", {})

# ✅ Encouraged (goes through JARVIS)
jarvis.invoke_tool("stitch", "get_exercise", {})
```

---

## Request/Response Flow

### Example 1: JARVIS Requests Quiz from Lilo

```
User Input: "Give me a Security+ practice question"
  ↓
JARVIS.process()
  ├─ Step 1: Parse input → intent: "quiz"
  ├─ Step 2: Determine agent → agent: "lilo"
  └─ Step 3: Invoke tool
      ↓
      ToolDispatcher.dispatch(
        ToolRequest(
          tool_name="lilo",
          action="get_question",
          params={}
        )
      )
      ├─ Service lookup: "lilo" found ✅
      ├─ Capability lookup: "get_question" exists ✅
      ├─ Service available: yes ✅
      └─ Invoke: lilo.get_question()
          ↓
          Returns: {
            "question": "What is defense in depth?",
            "options": [...],
            "difficulty": "intermediate"
          }
      ↓
      ToolResponse(
        success=true,
        result={...}
      )
  ↓
ReasoningTrace emitted with step:
  {
    "action": "invoke_tool",
    "tool_name": "lilo",
    "action": "get_question",
    "confidence": 0.95
  }
```

### Example 2: Stitch Cross-References Lilo for Security Context

```
User Code: "Implement password hashing"
  ↓
Stitch.process()
  ├─ Step 1: Analyze code → needs_security_context
  ├─ Step 2: Request context from Lilo
  └─ Step 3: Invoke tool
      ↓
      ToolDispatcher.dispatch(
        ToolRequest(
          tool_name="lilo",
          action="get_question",
          params={"topic": "cryptography"}
        )
      )
      ├─ Returns related security concepts
      └─ Stitch provides contextual feedback
```

---

## Phase 3: Memory Bridge Integration

When agents emit reasoning traces, they flow through the Event System:

```
Agent.process()
  ├─ Generate response
  ├─ Build ReasoningTrace
  └─ emit_reasoning_trace()
      ↓
      EventClient.publish_event(
        source="jarvis",
        event_type="reasoning_trace",
        payload={...trace...}
      )
      ↓
      O.B.E.O.S Event API
      ├─ Store in EventStore (immutable)
      ├─ Index in SQL (queryable)
      └─ Embed in ChromaDB (semantic search)
```

This enables:
- **Audit Trail:** Every decision logged
- **Learning:** Semantic search over past reasoning
- **Orchestration:** O.B.E.O.S can coordinate agents based on event patterns

---

## Implementation Checklist

- [x] Agent base class with standardized interface
- [x] ToolDispatcher with capability-aware routing
- [x] JARVISAgent implementation
- [x] LiloAgent implementation
- [x] StitchAgent implementation
- [x] Phase 2 test verification
- [ ] **Phase 3:** Memory bridge - connect Event API to agents
- [ ] **Phase 3:** Emit reasoning traces as events
- [ ] **Phase 3:** Test cross-agent tool invocation
- [ ] **Phase 4:** O.B.E.O.S orchestration engine
- [ ] **Phase 4:** Intelligent work distribution
- [ ] **Phase 4:** Multi-agent workflows

---

## Future Enhancements

### Dynamic Tool Registration
Services could register new tools at runtime:
```python
registry.register_capability(
  service="jarvis",
  capability="generate_report",
  input_schema={...},
  output_schema={...}
)
```

### Tool Schemas & Validation
Enforce strict input/output validation:
```python
# Tool request validated against schema
ToolRequest.validate_against_schema(tool_name, action)
```

### Rate Limiting
Prevent agents from overwhelming services:
```python
@rate_limit(calls=100, period=60)
def invoke_tool(...):
    ...
```

### Tool Caching
Cache frequent tool invocations:
```python
@cache(ttl=300)  # 5 min cache
def retrieve_question(topic):
    ...
```

---

**Last Updated:** Phase 2 Completion
**Next Phase:** Phase 3 - Memory Bridge Integration
