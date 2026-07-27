# BlackForge

[![CI](https://github.com/5K-bit/Blackforge/actions/workflows/ci.yml/badge.svg)](https://github.com/5K-bit/Blackforge/actions/workflows/ci.yml)

BlackForge is a local-first project scaffolding tool for developers. It creates
clean starter projects from reusable templates.

## Features

- `blackforge list-templates`
- `blackforge new cli PROJECT_NAME`
- `blackforge new fastapi PROJECT_NAME`
- `blackforge new python-package PROJECT_NAME`
- Overwrite protection with `--force`
- Jinja2-backed template rendering with:
  - `project_name`
  - `package_name`
  - `created_at`

## Installation

```bash
pip install -e ".[dev]"
```

## Usage

```bash
blackforge list-templates
blackforge new cli my-tool
blackforge new fastapi my-api
blackforge new python-package my-package
```

## Development

Run tests:

```bash
pytest
```
