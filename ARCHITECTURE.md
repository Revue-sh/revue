# Architecture Guidelines

**Project:** Revue.io  
**Philosophy:** Modular Monolith with Microservices-Ready Design  
**Updated:** 2026-03-31

---

## Core Principle

> **Start simple. Stay modular. Scale when needed.**

We build a **modular monolith** — a single deployable application with clear internal boundaries that could become independent services if scaling demands it.

---

## Why Not Microservices Now?

**Current reality:**
- Single developer / small team
- Dev-facing tooling, not public SaaS
- Predictable load (dozens of reviews/day, not millions)
- Local deployment (Docker + CLI)

**Microservices cost:**
- Network latency & distributed debugging
- Deployment complexity (multiple repos, containers, orchestration)
- Data consistency challenges (distributed transactions)
- Organizational overhead (team boundaries, service contracts)

**Decision:** Build modular monolith now. Extract services when pain emerges (e.g., scaling bottlenecks, team growth, independent deployment needs).

---

## SOLID Principles (Project-Wide Standard)

Every module, service, and component MUST follow these principles:

### 1. Single Responsibility Principle (SRP)
**Each class/module has ONE reason to change.**

```python
# ❌ BAD: CLI command contains SQL and formatting
@click.command()
def list_reviews():
    conn = psycopg2.connect(...)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM reviews")
    rows = cursor.fetchall()
    print(tabulate(rows))  # Mixed concerns!

# ✅ GOOD: Separated concerns
@click.command()
def list_reviews():
    service = ReviewService(ReviewRepository(get_db_connection()))
    reviews = service.get_all_reviews()  # Business logic
    ReviewFormatter.print_table(reviews)  # Presentation
```

### 2. Open/Closed Principle (OCP)
**Open for extension, closed for modification.**

```python
# ✅ Add new repositories without changing service
class ReviewService:
    def __init__(self, review_repo: ReviewRepository, quality_repo: QualityRepository):
        self.review_repo = review_repo
        self.quality_repo = quality_repo  # New repo, no service rewrite
```

### 3. Liskov Substitution Principle (LSP)
**Mock implementations must substitute real ones seamlessly.**

```python
# ✅ Test with mock repository
class MockReviewRepository(ReviewRepository):
    def list_reviews(self):
        return [Review(ticket_id="TEST-1", model="gpt-4", finding_count=5)]

# Test uses mock; production uses real DB — same interface
```

### 4. Interface Segregation Principle (ISP)
**Many small interfaces > one large interface.**

```python
# ✅ Focused interfaces
class IReviewReader(ABC):
    @abstractmethod
    def list_reviews(self) -> List[Review]: pass

class IReviewWriter(ABC):
    @abstractmethod
    def create_review(self, review: Review) -> int: pass

# CLI only needs reader; importer only needs writer
```

### 5. Dependency Inversion Principle (DIP)
**Depend on abstractions, not concretions.**

```python
# ❌ BAD: Service depends on concrete DB implementation
class ReviewService:
    def __init__(self):
        self.conn = psycopg2.connect(DATABASE_URL)  # Hardcoded!

# ✅ GOOD: Service depends on repository abstraction
class ReviewService:
    def __init__(self, repo: IReviewRepository):  # Injected interface
        self.repo = repo
```

---

## Layered Architecture

```
┌─────────────────────────────────────────────┐
│  Presentation Layer (CLI / API / TUI)      │  ← User-facing
├─────────────────────────────────────────────┤
│  Service Layer (Business Logic)            │  ← Domain rules
├─────────────────────────────────────────────┤
│  Repository Layer (Data Access)            │  ← DB abstraction
├─────────────────────────────────────────────┤
│  Infrastructure (Postgres / External APIs) │  ← Raw tech
└─────────────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Responsibility | Examples |
|-------|---------------|----------|
| **Presentation** | User interaction, formatting, validation | `cli/reviews.py`, `api/routes.py` |
| **Service** | Business logic, orchestration, domain rules | `reviews/service.py`, `quality/scorer.py` |
| **Repository** | Data access, query abstraction, persistence | `db/repositories/review_repository.py` |
| **Infrastructure** | External systems, DB connections, APIs | `db/connection.py`, `external/openai_client.py` |

### Rules
- **Presentation** calls **Service** (never Repository directly)
- **Service** calls **Repository** (never raw SQL)
- **Repository** calls **Infrastructure** (DB connections, external APIs)
- **No layer skipping** (CLI must not call DB directly)

---

## Domain Models vs. Data Models

**Principle:** Domain logic operates on domain objects, not database schemas.

### Example: Review Domain

```python
# ❌ BAD: CLI uses DB row tuples
cursor.execute("SELECT ticket_id, model, created_at FROM reviews")
for row in cursor.fetchall():
    print(f"{row[0]} - {row[1]}")  # Fragile! Column order matters

# ✅ GOOD: Repository returns domain objects
@dataclass
class Review:
    """Domain model — clean, typed, testable"""
    ticket_id: str
    model: str
    created_at: datetime
    finding_count: int

class ReviewRepository:
    def list_reviews(self) -> List[Review]:
        rows = self._execute("SELECT ticket_id, model, created_at FROM reviews")
        return [Review(**row) for row in rows]  # Map DB → domain

# CLI works with domain objects
reviews = service.get_all_reviews()
for review in reviews:
    print(f"{review.ticket_id} - {review.model}")  # Type-safe!
```

**Benefits:**
- Type safety (IDE autocomplete, static analysis)
- Testability (mock domain objects, not DB rows)
- Refactoring safety (change DB schema without breaking business logic)
- Migration readiness (swap DB → API without changing domain models)

---

## Repository Pattern (Data Access Abstraction)

**Goal:** Hide SQL/DB details behind clean interfaces.

### Structure

```
src/db/repositories/
  base.py              # BaseRepository (shared connection logic)
  review_repository.py # ReviewRepository(BaseRepository)
  finding_repository.py
  quality_repository.py
```

### Template

```python
# src/db/repositories/base.py
from abc import ABC, abstractmethod
from typing import Any

class BaseRepository(ABC):
    def __init__(self, connection):
        self.conn = connection
    
    def _execute(self, query: str, params: tuple = ()) -> list:
        """Execute query, return rows as dicts"""
        with self.conn.cursor() as cursor:
            cursor.execute(query, params)
            if cursor.description:  # SELECT query
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        return []

# src/db/repositories/review_repository.py
class ReviewRepository(BaseRepository):
    def list_reviews(self, limit: int = 100, offset: int = 0) -> List[Review]:
        rows = self._execute(
            """
            SELECT r.ticket_id, m.name AS model, r.created_at,
                   COUNT(f.id) AS finding_count
            FROM reviews r
            JOIN models m ON r.model_id = m.id
            LEFT JOIN findings f ON f.review_id = r.id
            GROUP BY r.id, m.name
            ORDER BY r.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset)
        )
        return [Review(**row) for row in rows]
    
    def get_by_ticket(self, ticket_id: str) -> Optional[Review]:
        rows = self._execute(
            "SELECT * FROM reviews WHERE ticket_id = %s",
            (ticket_id,)
        )
        return Review(**rows[0]) if rows else None
```

**Why this works:**
- SQL isolated in repository (business logic never sees queries)
- Easy to mock for testing (`MockReviewRepository`)
- Can swap Postgres → different DB by implementing same interface
- Can swap DB → HTTP API by implementing same interface

---

## Service Layer (Business Logic)

**Goal:** Encapsulate domain rules, orchestrate repositories.

### Structure

```
src/reviews/
  service.py           # ReviewService (orchestrates repos)
  models.py            # Domain models (Review, Finding, etc.)
```

### Template

```python
# src/reviews/service.py
class ReviewService:
    def __init__(self, review_repo: ReviewRepository, finding_repo: FindingRepository):
        self.review_repo = review_repo
        self.finding_repo = finding_repo
    
    def get_all_reviews(self, limit: int = 100) -> List[Review]:
        """Business logic: fetch reviews with enriched data"""
        reviews = self.review_repo.list_reviews(limit=limit)
        # Could enrich with additional data, apply business rules, etc.
        return reviews
    
    def get_review_details(self, ticket_id: str) -> Optional[ReviewDetail]:
        """Aggregate data from multiple repos"""
        review = self.review_repo.get_by_ticket(ticket_id)
        if not review:
            return None
        findings = self.finding_repo.get_by_review(review.id)
        return ReviewDetail(review=review, findings=findings)
```

**Why this matters:**
- Business logic in one place (not scattered across CLI/API/TUI)
- Testable without DB (inject mock repos)
- Reusable across presentation layers (CLI + future API use same service)

---

## Dependency Injection

**Goal:** Pass dependencies via constructor, not globals/hardcoded.

### ❌ Bad (Tight Coupling)

```python
# Hardcoded DB connection — can't test, can't swap
class ReviewService:
    def __init__(self):
        self.conn = psycopg2.connect(DATABASE_URL)  # Global dependency!
    
    def get_reviews(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM reviews")
        return cursor.fetchall()
```

### ✅ Good (Dependency Injection)

```python
# Dependencies injected — testable, swappable
class ReviewService:
    def __init__(self, review_repo: ReviewRepository):
        self.review_repo = review_repo  # Injected!
    
    def get_reviews(self):
        return self.review_repo.list_reviews()

# Production wiring (CLI/API)
conn = get_db_connection()
repo = ReviewRepository(conn)
service = ReviewService(repo)

# Test wiring
mock_repo = MockReviewRepository()
service = ReviewService(mock_repo)  # Same interface, no DB needed
```

---

## Migration Path to Microservices

**When to extract a service:**
1. **Scaling bottleneck** — One domain needs 10x more resources
2. **Team boundaries** — Different teams own different domains
3. **Independent deployment** — Need to deploy reviews module without deploying quality module
4. **Technology constraints** — One domain needs different stack (e.g., Go for performance)

**How to extract (example: Review Service):**

### Step 1: Current State (Modular Monolith)
```
CLI → ReviewService → ReviewRepository → Postgres
```

### Step 2: Extract Service to HTTP API
```
# New microservice repo
api/
  routes/review_routes.py     # FastAPI routes
  services/review_service.py  # SAME business logic (copy from monolith)
  repositories/               # SAME repositories (copy from monolith)

# Start HTTP server
uvicorn api.main:app --port 8001
```

### Step 3: Update CLI to Call API
```python
# Before (direct DB)
conn = get_db_connection()
repo = ReviewRepository(conn)
service = ReviewService(repo)

# After (HTTP API)
class HttpReviewRepository(ReviewRepository):
    def list_reviews(self):
        response = httpx.get("http://localhost:8001/reviews")
        return [Review(**r) for r in response.json()]

repo = HttpReviewRepository()  # Same interface!
service = ReviewService(repo)  # No service changes needed
```

**Key insight:** Because we used repository pattern, CLI code doesn't change — only the repository implementation swaps from DB to HTTP.

---

## Code Review Checklist

Every PR MUST satisfy these checks:

### Architecture Compliance
- [ ] **No raw SQL in CLI/API/Service layers** (only in repositories)
- [ ] **Domain models separate from DB schemas** (no tuples/dicts in business logic)
- [ ] **Dependencies injected via constructor** (no global DB connections)
- [ ] **Repositories inherit from BaseRepository** (consistent interface)
- [ ] **Services orchestrate repos, not DB** (no psycopg2 imports in services)

### SOLID Principles
- [ ] **Single Responsibility:** Each class has one clear purpose
- [ ] **Open/Closed:** Can extend without modifying existing code
- [ ] **Liskov Substitution:** Mock implementations work seamlessly
- [ ] **Interface Segregation:** Interfaces are focused (not monolithic)
- [ ] **Dependency Inversion:** Depend on abstractions, not concretions

### Testing
- [ ] **Unit tests mock repositories** (prove service layer is testable)
- [ ] **Integration tests use real DB** (prove repository layer works)
- [ ] **No DB calls in unit tests** (proves proper abstraction)

### Quality Gates
- [ ] Code passes linters (ruff, mypy)
- [ ] All tests pass (540+ tests in CI)
- [ ] Documentation updated (if public API changes)

---

## File Structure (Example: REVUE-91)

```
src/
  cli/
    reviews.py                    # Click CLI (presentation layer)
  reviews/
    service.py                    # ReviewService (business logic)
    models.py                     # Domain models (Review, ReviewDetail)
  db/
    repositories/
      base.py                     # BaseRepository
      review_repository.py        # ReviewRepository
      finding_repository.py       # FindingRepository
    connection.py                 # get_db_connection() utility

tests/
  cli/
    test_reviews.py               # CLI integration tests
  reviews/
    test_service.py               # Service unit tests (mock repos)
  db/
    test_review_repository.py     # Repository integration tests (real DB)
```

---

## Data Persistence Strategy

### Local JSON Storage (MVP - Current)

While the architecture above describes the target layered design with database-backed repositories, the MVP uses a simpler persistence model to validate the product before introducing infrastructure dependencies.

**Decision (2026-04-03):** Store review metadata, quality scores, and agent analytics in JSON files under `.revue/` folder in the local repository.

**Rationale:**
- MVP simplicity — no external infrastructure needed
- Version-controlled alongside code (except ephemeral analytics)
- Local developer workflow (no network dependencies)
- Easy to inspect and debug
- Migration is transparent via repository abstraction layer (swap JSON → DB implementation without changing business logic)

**Structure:**
```
.revue/
  reviews/
    REVUE-123.json          # Review metadata (committed)
    REVUE-124.json
  quality/
    scores.json             # Quality score history (committed)
  analytics/
    agent_usage.json        # Agent performance metrics (.gitignored)
```

**JSON Schema:** Follows the domain models defined in `src/revue/models.py` (e.g., `Review`, `Finding`, `QualityScore`).

**Concurrency:** Single-writer assumption — concurrent access (e.g., CI + local dev writing simultaneously) is out of scope for MVP. If this becomes a pain point, migrate to PostgreSQL.

**Git Configuration:**
- `.revue/reviews/` and `.revue/quality/` are committed (historical record)
- `.revue/analytics/` is `.gitignore`d (ephemeral local metrics)

**Future Migration Path:**

When query complexity or multi-user access outgrows flat files, migrate via repository abstraction:
- **Reviews data** → PostgreSQL (likely to outgrow JSON first — complex joins, filtering)
- **Quality scores** → PostgreSQL or cloud storage (S3, Supabase)
- **Analytics** → Time-series DB or analytics service (DataDog, Posthog)
- **Hybrid option:** JSON for local dev, DB for CI/production

The repository pattern (described above) makes this swap transparent to business logic — swap `JsonReviewRepository` → `PostgresReviewRepository` without changing service layer.

---

## Real-World Example (Coming Soon)

Once REVUE-91 is implemented, this section will show concrete code examples from production.

---

## References

- [SOLID Principles (Uncle Bob)](https://blog.cleancoder.com/uncle-bob/2020/10/18/Solid-Relevance.html)
- [Repository Pattern (Martin Fowler)](https://martinfowler.com/eaaCatalog/repository.html)
- [Modular Monolith (Simon Brown)](https://www.youtube.com/watch?v=5OjqD-ow8GE)

---

**Remember:** Architecture serves the business, not ego. Build what you need today, prepare for what you might need tomorrow.
