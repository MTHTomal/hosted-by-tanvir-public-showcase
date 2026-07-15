# Hosted by Tanvir

**Hosted by Tanvir** is a deployed, server-rendered Django platform for operating community e-football tournaments.

It was independently built, deployed, and maintained by **Mohammad Tanvir Hossain**. The platform has been used to operate a real community tournament involving approximately **64 matches**, with public fixtures, approved results, standings, player statistics, and team histories.

**Live application:** [hosted-by-tanvir.onrender.com](https://hosted-by-tanvir.onrender.com/)

The public tournament pages can be explored without a player or staff account.

---

## Why I Built It

The project began as a solution to a real community problem.

Generic tournament platforms did not fully support the operational requirements of our e-football events, including:

- larger community tournaments
- co-op teams with multiple players
- player-level statistics
- roster and team-membership history
- evidence-backed result submissions
- staff-controlled result approval
- player recruitment and free-agent discovery
- historical tournament and head-to-head records

Rather than adapting the tournament around the limitations of a generic service, I built a platform around the community's actual workflow.

Hosted by Tanvir began as a personal project and was later extended for **CSE449: Distributed, Parallel and HPC Systems**.

---

## Core Tournament Workflow

The primary validated operational path is the team-based tournament workflow:

```text
Player and team registration
        -> tournament entrant review
        -> seed and group assignment
        -> fixture generation and scheduling
        -> result submission with evidence
        -> optional opponent response
        -> staff moderation
        -> approved official result
        -> standings and official player statistics
```

Only staff-approved database records become official results and statistics.

Uploaded screenshots are retained as review evidence. They are not treated as the authoritative structured-data source.

---

## Main Features

### Tournament Operations

- Public tournament browsing
- Explicit tournament registration and entrant review
- Team-based registration and roster-eligibility checks
- Manual seeding and group assignment
- Round-robin tournaments
- Knockout tournaments
- Grouped tournaments
- Progressive hybrid tournaments
- Bye handling
- Fixture-generation safeguards
- Fixture scheduling and submission deadlines
- Tournament archives and historical visibility

### Results and Moderation

- Result submission by participating players
- Screenshot evidence uploads
- Pending-result privacy controls
- Optional opponent score response
- Advisory score-match and score-conflict indicators
- Staff approval, rejection, dispute, editing, and correction
- Transactional official-result approval
- Publication of official player statistics after approval
- Standings recalculation from approved results

### Players, Teams, and Marketplace

- Custom player accounts and profiles
- Teams and captains
- Team-membership history
- Recruitment availability
- Team recruiting status
- Captain-to-player invitations
- Player invitation acceptance and rejection
- Staff roster assignment
- Public player and team profiles
- Career and tournament-level statistics

### Communication and Staff Operations

- In-app notifications
- Public announcements
- Private complaints and requests
- Staff response workflow
- Site-native staff dashboard
- Tournament and entrant management
- Result-moderation queues
- Staff CSV exports
- Staff-only prediction dataset ZIP export

Django admin remains available for exceptional inspection and correction, but routine tournament operations are handled through the platform's own staff interface.

---

## Architecture

Hosted by Tanvir uses a Django-centered architecture rather than a separate frontend application and backend API.

### Django Applications

#### `accounts`

Responsible for:

- custom `Player` user model
- teams and captains
- `TeamMembership` roster history
- marketplace workflows
- team invitations
- in-app notifications

#### `tournament`

Responsible for:

- tournaments and registrations
- fixtures and scheduling
- result submission and moderation
- complaints and announcements
- grouped and hybrid progression
- CSV and ZIP exports

#### `standings`

Responsible for:

- team standings
- official player statistics
- career and historical aggregation services
- leaderboards
- head-to-head calculations

### Production Architecture

```text
Browser
   |
   v
Django + Gunicorn on Render
   |
   +--> PostgreSQL on Supabase
   |
   +--> Cloudinary media storage
   |
   +--> WhiteNoise static-file serving
```

The application uses:

- Django templates for server-rendered pages
- HTMX for selected dynamic interactions
- locally compiled Tailwind CSS
- PostgreSQL through Supabase
- Cloudinary for uploaded media
- WhiteNoise for collected static files
- Gunicorn as the production WSGI server
- Render for web deployment

PostgreSQL is the authoritative source of truth for tournament data.

---

## Important Technical Decisions

### Django-Native Application Instead of a Split Frontend

An earlier version considered a separately deployed frontend.

That approach created unnecessary deployment complexity and additional build usage for a project whose core logic, authentication, forms, and views were already Django-centered.

The final architecture keeps routing, authentication, templates, business logic, and deployment inside Django.

This made the platform:

- easier to deploy
- easier to maintain
- better aligned with the Python-centered codebase
- less dependent on separate hosting services
- more appropriate for the project's scale

### PostgreSQL and Supabase Instead of Firebase

Tournament data is highly relational.

Players, teams, memberships, registrations, fixtures, results, standings, and statistics require joins, constraints, aggregation, and transactional integrity.

PostgreSQL was selected because it provides:

- relational integrity
- expressive Django ORM queries
- database constraints
- reliable transaction support
- clearer historical and statistical queries

Firebase and Firestore were considered, but their document-oriented structure would have made the tournament relationships and statistical queries more complicated.

### Local Tailwind Build Instead of Tailwind CDN

The original plan used Tailwind through a CDN.

During implementation, the project moved to a local Tailwind build because it provides:

- consistent utility generation
- class purging
- predictable production styling
- no runtime styling dependency on a CDN

The compiled CSS is served through Django static files and WhiteNoise.

### Site-Native Staff Interface Instead of Relying on Django Admin

The original plan expected Django admin to handle most operational tasks.

During development, it became clear that tournament staff needed contextual workflows inside the product itself.

The platform therefore added site-native interfaces for:

- entrant review
- group assignment
- fixture generation
- scheduling
- result moderation
- announcements
- complaints
- marketplace management
- exports

Django admin is now primarily a fallback and recovery surface.

### Synchronous Correctness Before Background Processing

Celery and Redis were added as a task-queue foundation, but critical tournament correctness remains synchronous.

Result approval, official-stat publication, and standings recalculation do not depend on a worker being available.

This was a deliberate reliability decision:

```text
Core correctness -> synchronous and transactional
Optional side effects -> eligible for background processing
```

### Structured Approved Data Instead of OCR as the Primary Source

The live application collects structured result and player-stat data through forms.

Screenshots are used as supporting evidence for staff review.

Approved database rows remain canonical because structured input is more reliable than treating OCR output as automatic truth.

OCR experiments were developed later for local review and performance research, not for automatically modifying official statistics.

---

## Engineering Highlights

### Relational Integrity

The data model uses database constraints and validation to protect important rules, including:

- active team-membership restrictions
- valid tournament-registration targets
- roster capacity
- duplicate-registration prevention
- marketplace invitation state
- one canonical approved result per fixture

### Explicit Entrant Management

`TournamentRegistration` is the canonical tournament entrant layer.

This separates the existence of a player or team from participation in a particular tournament and allows staff to control:

- approval
- activation
- seeding
- grouping
- tournament structure

### Team-Membership History

`TeamMembership` preserves when a player joined or left a team.

This supports:

- current rosters
- historical rosters
- team history
- career statistics
- movement between teams without deleting previous relationships

### Transactional Result Approval

Official result approval uses database transactions and row locking.

The approval process protects:

- canonical approved-result selection
- official player-stat publication
- standings recalculation
- correction of previously approved results
- consistency during competing operations

### Pending and Official Data Boundaries

Pending result submissions and submitted player statistics remain review-stage data.

Only approved results and published `PlayerStat` records are used for:

- public standings
- player leaderboards
- career statistics
- exports
- prediction inputs

### Permission and Privacy Controls

The application includes:

- role-based authorization
- staff-only operational routes
- player ownership checks
- fixture-participant checks
- complaint ownership protection
- private evidence visibility
- draft-tournament hiding
- upload file-type and size validation

---

## CSE449 Systems Extension

The project was later used as the foundation for experiments in distributed systems, parallel programming, GPU computing, and performance measurement.

### Stable Systems Components in This Repository

- Celery application configuration
- Redis broker configuration
- optional asynchronous notification wrappers
- deterministic fixture estimates
- approved-data prediction ZIP export
- standalone ZIP package validation
- local deterministic baseline processing
- sequential CPU benchmark mode
- multiprocessing CPU benchmark mode
- optional local PyTorch CPU/CUDA availability probes
- JMeter public-route test plans
- sanitized JMeter result summarization

### Celery and Redis Boundary

Celery and Redis form a tested local task-queue foundation, not a deployed production worker service.

```text
Django creates a task message
        -> Redis stores the message
        -> Celery workers consume independent tasks
        -> workers load authoritative data when needed
```

Redis does not store authoritative tournament data. PostgreSQL remains the source of truth.

Core result approval, standings, and official-stat publication remain synchronous.

### Prediction Boundary

The fixture estimate is:

- deterministic
- transparent
- formula-based
- CPU-oriented
- derived from approved historical records

It is not a trained machine-learning model and is not presented as an AI prediction system.

### Parallel Processing

The local prediction tooling includes a benchmark comparison between:

- sequential CPU execution
- multiprocessing CPU execution using independent worker processes

This provides a controlled foundation for evaluating when process-level parallelism improves a workload and when process-management overhead reduces its benefits.

### Load Testing

The repository includes:

- local JMeter public-route test plans
- safe GET-only route coverage
- latency, throughput, and error-rate collection
- a Python result summarizer
- sanitized CSV, JSON, and Markdown summaries

These tools are intended for local performance investigation. Their results are not presented as guarantees of production capacity.

### Separate OCR and GPU Experiment

A later local CSE449 experiment evaluated:

- EasyOCR screenshot extraction
- sequential CPU processing
- six-process CPU processing
- single-worker GPU inference
- multi-process GPU inference
- JMeter load testing
- Celery screenshot-task distribution

The experiment demonstrated an important systems lesson: **adding more workers does not always improve performance**.

Multiple GPU processes duplicated model loading and competed for shared GPU memory. A single GPU worker provided the most effective configuration for that OCR workload.

It is not part of the deployed application's official-stat workflow, and OCR output does not automatically modify official tournament data.

---

---

## Technology Stack

### Application

- Python
- Django
- Django templates
- HTMX
- Tailwind CSS
- Pillow

### Data and Storage

- PostgreSQL
- Supabase
- SQLite for simple local development
- Cloudinary
- CSV and ZIP export tooling

### Deployment

- Render
- Gunicorn
- WhiteNoise

### Systems and Performance Work

- Celery
- Redis
- Python multiprocessing
- optional PyTorch and CUDA probes
- Apache JMeter

---

## Local Setup

### 1. Clone the Repository

```bash
git clone https://github.com/MTHTomal/hosted-by-tanvir-public-showcase.git hosted-by-tanvir
cd hosted-by-tanvir
```


### 2. Create a Virtual Environment

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm install
```

### 4. Configure the Environment

Copy the environment template.

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS or Linux:

```bash
cp .env.example .env
```

For the simplest local setup:

```env
DEBUG=1
DATABASE_URL=sqlite:///db.sqlite3
ALLOWED_HOSTS=localhost,127.0.0.1
```

External Cloudinary, SMTP, and Redis integrations can remain disabled unless those features are being tested.

### 5. Prepare the Application

```bash
python manage.py migrate
npm run build:css
```

### 6. Run the Development Server

```bash
python manage.py runserver
```

Create an optional staff account with:

```bash
python manage.py createsuperuser
```

For continuous Tailwind compilation during frontend work:

```bash
npm run watch:css
```

---

## Environment Variables

The repository includes a placeholder-only `.env.example`.

Important configuration groups include:

- **Django:** `DEBUG`, `SECRET_KEY`, `ENV_FILE`
- **Database:** `DATABASE_URL`
- **Hosts and security:** `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SECURE_SSL_REDIRECT`
- **Render:** `RENDER_EXTERNAL_HOSTNAME`
- **Cloudinary:** `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`
- **Celery and Redis:** `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `NOTIFICATIONS_USE_CELERY`
- **Email:** Django email-backend and SMTP variables
- **Optional community link:** `DISCORD_LINK`

Real credentials, databases, user-uploaded evidence, private exports, and environment files must not be committed.

---

## Testing

Run the standard project checks with:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

The repository includes automated coverage for:

- account and profile behavior
- permissions and ownership
- tournament registration
- fixture generation
- grouped and hybrid workflows
- result submission and moderation
- evidence privacy and validation
- standings and player statistics
- marketplace behavior
- complaints and announcements
- notifications and Celery foundations
- CSV and ZIP exports
- prediction package tooling
- benchmark helpers
- JMeter result summarization

A current test count should only be published after the suite is rerun from a clean clone.

---

## Deployment

The production web application is deployed on Render.

- Gunicorn runs the Django WSGI application.
- WhiteNoise serves collected static files.
- PostgreSQL through Supabase stores authoritative relational data.
- Cloudinary stores uploaded media.
- Tailwind CSS is compiled locally and committed as static output.
- Environment variables provide production secrets and host configuration.

The deployed application does not currently require or claim a production Celery worker.

---

## Current Scope and Limitations

- The team-based tournament workflow is the primary validated operational path.
- Single-player tournament support is not at full end-to-end parity.
- The project does not currently expose a meaningful Django REST Framework API.
- Fixture prediction is deterministic and formula-based rather than AI or machine learning.
- Celery and Redis are optional and are not required for core correctness.
- Local JMeter tests and benchmark tools do not prove production capacity.
- OCR output is experimental candidate data and does not automatically modify official statistics.
- Monte Carlo and Gemma-based analytics were proposed but were not completed as part of the stable application.

---

## Development Approach

The project was independently designed, integrated, tested, deployed, operated, and maintained by **Mohammad Tanvir Hossain**.

AI-assisted tools—including Codex, ChatGPT, Claude, DeepSeek, and Microsoft Copilot—were used for implementation support, debugging assistance, code review, research, and design exploration.

The author remained responsible for:

- requirements
- architecture decisions
- feature prioritization
- code integration
- testing and verification
- security decisions
- deployment
- maintenance
- real tournament operations
