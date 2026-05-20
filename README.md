# O.B.E.O.S.

Official Blackfong Environment Operating System is a local-first engine scaffold for modular operator tooling, job orchestration, and terminal-driven system control.

## What It Is

O.B.E.O.S. is a Python-based runtime scaffold built around one long-lived engine process. That engine owns local state, accepts commands over a Unix socket, loads modules, and exposes a small CLI surface through `bf`.

Current repo components include:

- an engine with SQLite-backed state
- a local event bus and scheduler
- module loading for `build`, `control`, `observe`, and `memory`
- a Unix socket IPC layer
- a `bf` command-line client
- a GTK console scaffold
- a systemd unit for host installation

## Why It Exists

This project exists to explore a Blackfong-style operator environment where local tools are coordinated through a single runtime instead of scattered scripts and disconnected utilities.

## Problem It Solves

Many local automation setups grow into ad hoc command collections with no shared state, weak observability, and inconsistent control flow. O.B.E.O.S. is a practical attempt to solve that by giving local modules one engine, one state store, and one control path.

## Features

- long-running engine process with local state persistence
- Unix socket command transport for local clients
- module-based command routing
- internal event bus and job scheduler
- CLI client for status, jobs, logs, memory, and shutdown
- optional desktop console scaffold
- systemd packaging for Linux host startup

## Tech Stack

- Python 3
- SQLite
- PyYAML
- Unix domain sockets
- systemd unit packaging
- GTK console scaffold

## Quick Start

Install the current dependency set:

```bash
python3 -m pip install -r requirements.txt
```

Run the engine from the repo with a local development root and socket:

```bash
export OBEOS_ROOT="$(pwd)/opt/obeos"
export OBEOS_SOCKET="/tmp/obeos.sock"
python3 opt/obeos/engine/engine.py
```

In another shell, connect with the CLI:

```bash
export OBEOS_SOCKET="/tmp/obeos.sock"
python3 opt/obeos/cli/bf status
python3 opt/obeos/cli/bf build obeos amd64 minimal --follow
python3 opt/obeos/cli/bf jobs 20
python3 opt/obeos/cli/bf logs last 1h
python3 opt/obeos/cli/bf memory summary
python3 opt/obeos/cli/bf shutdown
```

For a host-style install, the runtime layout mirrors `/opt/obeos`, and the systemd unit lives at `packaging/systemd/blackfong-engine.service`.

## CLI / API Usage

Primary commands:

- `python3 opt/obeos/cli/bf status`
- `python3 opt/obeos/cli/bf build obeos amd64 minimal --follow`
- `python3 opt/obeos/cli/bf jobs 20`
- `python3 opt/obeos/cli/bf logs last 1h`
- `python3 opt/obeos/cli/bf memory summary`
- `python3 opt/obeos/cli/bf shutdown`

This repo currently exposes local socket-based control, not an HTTP API.

## Screenshots

No screenshots are published yet. The current emphasis is engine structure and local runtime behavior rather than polished presentation.

## Status

Current state: early but functional scaffold.

Implemented now:

- engine startup and shutdown lifecycle
- local SQLite state storage
- event bus, scheduler, and module loading
- local IPC transport over a Unix socket
- `bf` CLI for command dispatch and basic observability
- systemd unit scaffold for Linux deployment

Not complete yet:

- broader test coverage
- packaging and installation automation
- a mature desktop operator surface
- a larger set of production-ready modules

## Roadmap

- harden module contracts and permissions
- expand job and event observability
- improve installation and packaging workflow
- grow the operator console beyond scaffold status
- add test coverage around engine and module behavior

## Portfolio Note

O.B.E.O.S. is part of the Blackfong portfolio because it shows systems thinking rather than just interface work. It demonstrates how local-first tooling can be organized around a persistent engine, structured command routing, and modular runtime boundaries.
