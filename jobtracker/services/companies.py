"""Companies and their application process (steps)."""
from __future__ import annotations

import re

from ..models import Company, CompanyBook, GeneratedDocument, today
from ..process import GraphError, Node, Process, default_process
from ..storage import UserDocument
from . import files

store = UserDocument("companies.json", CompanyBook, seed_name="companies.json")


class NotFound(LookupError):
    pass


def _find(book: CompanyBook, company_id: str) -> Company:
    for c in book.companies:
        if c.id == company_id:
            return c
    raise NotFound(f"Company {company_id!r} not found")


# ------------------------------------------------------------------ queries

def list_companies(status: str | None = None, query: str | None = None) -> list[Company]:
    companies = store.load().companies
    if status:
        companies = [c for c in companies if c.status == status]
    if query:
        q = query.lower()
        companies = [c for c in companies if q in f"{c.name} {c.position} {c.location}".lower()]
    return companies


def get_company(company_id: str) -> Company:
    return _find(store.load(), company_id)


def find_by_name(name: str) -> Company | None:
    name = name.strip().lower()
    return next((c for c in store.load().companies if c.name.strip().lower() == name), None)


# ---------------------------------------------------------------- companies

def _clean_job_description(fields: dict, url: str = "") -> None:
    """A job description pasted or sent with the site around it (LinkedIn's buttons, similar jobs…) keeps the job."""
    if fields.get("job_description"):
        from . import jobs
        fields["job_description"] = jobs.clean_posting(fields["job_description"], fields.get("job_url") or url)


def create_company(**fields) -> Company:
    _clean_job_description(fields)
    if not fields.get("salary_expectation"):  # the profile's expectation, until set for this application
        from . import profile
        fields["salary_expectation"] = profile.settings().salary_expectation
    company = Company(**fields)
    _save_job_description(company)
    with store.transaction() as book:
        book.companies.append(company)
    if company.website:
        from . import logos
        logos.schedule(company.id)  # find its logo in the background
    return company


def update_company(company_id: str, fetch_logo: bool = True, **fields) -> Company:
    with store.transaction() as book:
        company = _find(book, company_id)
        _clean_job_description(fields, company.job_url)
        updated = company.model_validate({**company.model_dump(), **fields})
        _save_job_description(updated)
        book.companies[book.companies.index(company)] = updated
    if fetch_logo and updated.website and updated.website != company.website:
        from . import logos
        logos.schedule(updated.id)  # new website: look for its logo
    return updated


# ------------------------------------------------------- job description copy

JD_FILENAME = "Job_Description.md"


def _job_description_markdown(c: Company) -> str:
    meta = [f"- **Company:** {c.name}", f"- **Position:** {c.position}" if c.position else "",
            f"- **Posting:** {c.job_url}" if c.job_url else "", f"- **Saved:** {today()}"]
    return f"# {c.position or 'Job description'} – {c.name}\n\n" + "\n".join(m for m in meta if m) + \
        f"\n\n---\n\n{c.job_description.strip()}\n"


def _save_job_description(c: Company) -> None:
    """Keep a copy of the job description in the company folder (postings get deleted).
    Rewritten only when its content changes (not just the date); the previous copy goes to archive/."""
    if not c.job_description.strip():
        return
    path = files.output_folder(c.name) / JD_FILENAME
    content = _job_description_markdown(c)
    undated = lambda text: [line for line in text.splitlines() if not line.startswith("- **Saved:**")]
    if path.exists() and undated(path.read_text(encoding="utf-8")) == undated(content):
        c.job_description_file = files.relative(path)
        return
    files.archive_previous(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    c.job_description_file, c.job_description_saved_at = files.relative(path), today()


def save_job_description(ref: str) -> Company:
    """Write (or refresh) the saved copy of an application's job description."""
    company_id = resolve(ref).id
    with store.transaction() as book:
        company = _find(book, company_id)
        if not company.job_description.strip():
            raise ValueError(f"{company.name} has no job description to save")
        _save_job_description(company)
    return company


def delete_company(company_id: str) -> None:
    with store.transaction() as book:
        book.companies.remove(_find(book, company_id))


def log_update(company_id: str, by: str = "me", on: str | None = None, note: str = "") -> Company:
    """Record that you (`me`) or the company (`them`) gave news; resets the day counter."""
    with store.transaction() as book:
        company = _find(book, company_id)
        company.last_update = on or today()
        company.last_update_by = by
        if note:
            stamp = f"[{company.last_update} · {'me' if by == 'me' else 'them'}] {note}"
            company.notes = f"{company.notes}\n{stamp}".strip()
    return company


def add_news_report(company_id: str, report) -> Company:
    """How the user heard of news they entered (the newest 50 kept)."""
    with store.transaction() as book:
        company = _find(book, company_id)
        company.news_log = [*company.news_log, report][-50:]
    return company


def add_document(company_id: str, document: GeneratedDocument) -> Company:
    with store.transaction() as book:
        company = _find(book, company_id)
        company.documents.insert(0, document)
    return company


def current_stage(company: Company) -> str:
    """Where the application stands (see Process.stage)."""
    return company.process.stage()


# ------------------------------------------------------------ find by name

def find_applications(query: str) -> list[Company]:
    """Applications whose company name and/or position match the query, best first.

    "nimbus", "Nimbus AI scientist", "applied ai scientist" all work; an exact
    company name ranks first.
    """
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    if not words:
        return []
    scored = []
    for c in store.load().companies:
        name = c.name.lower()
        hay = re.split(r"\W+", f"{c.name} {c.position}".lower())
        hits = sum(any(h.startswith(w) for h in hay) for w in words)  # word prefixes: "nim" finds Nimbus
        if hits:
            exact = name == query.strip().lower() or name in query.lower()
            scored.append((exact, hits / len(words), c))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    best = [c for exact, ratio, c in scored if (exact, ratio) == scored[0][:2]] if scored else []
    return best + [c for _, _, c in scored if c not in best]


def resolve(ref: str) -> Company:
    """An application by id, or by company name / position when exactly one matches best."""
    book = store.load()
    for c in book.companies:
        if c.id == ref:
            return c
    found = find_applications(ref)
    if not found:
        raise NotFound(f"No application matches {ref!r}")
    words = [w for w in re.split(r"\W+", ref.lower()) if w]
    top = [c for c in found if c.name.lower() in ref.lower()] or \
          [c for c in found if all(any(h.startswith(w) for h in re.split(r"\W+", f"{c.name} {c.position}".lower()))
                                   for w in words)]
    if len(top) == 1:
        return top[0]
    if len(found) == 1:
        return found[0]
    raise NotFound(f"{ref!r} matches several applications: " +
                   "; ".join(f"{c.name} – {c.position} (id {c.id})" for c in (top or found)[:6]))


# ------------------------------------------------------------------ process

def _process_op(ref: str, op):
    """Load the application, apply `op(process)`, save, and return (company, op result)."""
    company_id = resolve(ref).id
    with store.transaction() as book:
        company = _find(book, company_id)
        try:
            result = op(company.process)
        except GraphError as e:
            raise ValueError(str(e)) from e
        company.status = company.process.status()
    return company, result


def add_step(ref: str, name: str, after: str | None = None, before: str | None = None,
             branch_from: str | None = None, join: str | None = None, state: str = "todo",
             date: str | None = None, notes: str = "", kind: str = "step", time: str | None = None,
             location: str = "", duration_min: int = 60) -> tuple[Company, Node]:
    node = Node(name=name, state=state, date=date, notes=notes, kind=kind, time=time, location=location,
                duration_min=duration_min)

    def op(p: Process):
        if branch_from:
            return p.add_branch(branch_from, node, join)
        if after and before:
            return p.insert_between(p.node(after).id, p.node(before).id, node)
        if after:
            return p.insert_after(after, node)
        if before:
            return p.insert_before(before, node)
        return p.append(node)
    return _process_op(ref, op)


def update_step(ref: str, node: str, **fields) -> tuple[Company, Node]:
    def op(p: Process):
        n = p.node(node)
        if "kind" in fields and (fields["kind"] == "closed") != (n.kind == "closed"):
            raise GraphError("The Closed node's type can't be changed, and only it can be 'closed'")
        if fields.get("state") == "done" and not fields.get("date") and not n.date:
            fields["date"] = today()  # marked done without a date: it happened today (counts for this week)
        for k, v in fields.items():
            setattr(n, k, v)
        if "category" in fields and n.kind != "closed":  # the "offer" type is what makes the status Offer
            n.kind = "offer" if n.category == "offer" else "step"
        return n
    return _process_op(ref, op)


def delete_step(ref: str, node: str) -> tuple[Company, Node]:
    return _process_op(ref, lambda p: p.remove(node))


def reset_process(ref: str) -> tuple[Company, Process]:
    def op(p: Process):
        fresh = default_process()
        p.nodes, p.edges = fresh.nodes, fresh.edges
        return p
    return _process_op(ref, op)
