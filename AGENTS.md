# Dongttok Agent Rules

## Architecture

Frontend
React / Vite / TypeScript

Main Backend
ASP.NET Core

RAG Server
Python / FastAPI

Storage
PostgreSQL
ChromaDB
Redis

## Core Principle

Every generated answer must be grounded in official
Dongguk University sources whenever the query requires
university-specific factual information.

Do not improve answer fluency at the expense of grounding.

## Development Rules

- Never modify main directly.
- Create a branch/worktree for every task.
- Run relevant tests before completion.
- Never commit secrets.
- Do not perform destructive DB operations.
- Do not deploy production without human approval.
- Preserve source attribution.
- Retrieval changes require regression testing.
