# OBEOS Integration & Stabilization Phase

Target: OBEOS v0.9 release candidate.

## Invariants

1. A child project with its own canonical repository is never maintained as a second editable copy inside OBEOS.
2. Every OBEOS component exposes `obeos.manifest.json` schema v1.
3. Component discovery is manifest-driven; DAISE internals are not imported by child projects.
4. Health crosses component boundaries through OBEOS Health Contract v1. MaxPayne is the health aggregation/remediation authority.
5. Events cross component boundaries through OBEOS Event Contract v1 and use namespaced event types.
6. DAISE reasoning is model-led. Retrieval, memory, system state, and tools are context inputs, never replacement intelligence.
7. Operator-facing identity is OBEOS / Blackfong / D.A.I.S.E. `jarvis` may remain only as an internal compatibility alias.
8. The shipping desktop doctrine is Kubuntu 24.04 LTS + KDE Plasma. XFCE/Xubuntu paths are migration input only.

## Ordered phase gates

- [x] Convert MaxPayne and 3000 embedded copies to canonical pinned submodules.
- [x] Add schema-v1 manifests to BlackForge, MaxPayne, Nightwatch, EarthGlass, The Assistant, 3000, and OBEOS core.
- [x] Add OBEOS component registry discovery/validation primitives.
- [x] Freeze Health Contract v1 data model and aggregate semantics.
- [x] Freeze Event Contract v1 envelope and namespaced event semantics.
- [x] Introduce an explicit model-led DAISE reasoning pipeline boundary.
- [ ] Cut all DAISE conversational execution over to the new reasoning boundary; remove retrieval-as-answer fallbacks.
- [ ] Wire MaxPayne to discover manifests and aggregate all declared health probes.
- [ ] Wire child services to emit Event Contract v1 envelopes into the Blackfong event spine.
- [ ] Run the full integration matrix on Windows, Kubuntu VM, cloud node, and Legion surface.
- [ ] Complete Kubuntu/Plasma installer implementation and remove shipping XFCE/Xubuntu behavior.
- [ ] Remove operator-visible `jarvis` copy and runtime labels.
- [ ] Produce a clean v0.9 RC evidence snapshot with all release gates green.

## Canonical child ownership

- BlackForge -> `5K-bit/Blackforge`
- MaxPayne -> `5K-bit/Max-Payne`
- Nightwatch -> `5K-bit/Night-watch-OS`
- EarthGlass -> `5K-bit/Earthglass`
- 3000 -> `5K-bit/3000`
- The Assistant -> `5K-bit/The-Assistant`

OBEOS Earth must adopt the same manifest/ownership contract when its canonical repository is published to the connected GitHub account.
