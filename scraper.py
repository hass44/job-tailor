"""Search LinkedIn's public job pages using the searches set on the Find jobs tab.

No LinkedIn account is used. Requests are paced at about one every 2.5 seconds, and the search backs
off when LinkedIn says to slow down. Every run stays on your own computer. Run it from the app, or:
    python app.py scrape
"""
import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"}
BASE = "https://www.linkedin.com/jobs-guest/jobs/api"
PACE_SECONDS = 2.5
LEVELS = ["Internship", "Entry level", "Associate", "Mid-Senior level", "Director", "Executive", "Not Applicable"]
DEFAULTS = {
    "queries": "", "locations": "", "days": 2, "max_pages": 10, "max_years": "",
    "exclude_title": "senior\nlead\nprincipal\nstaff\nhead of\ndirector\nvp\nintern\ninternship\n"
                     "working student\nwerkstudent\nfreelance",
    "include_title": "", "exclude_companies": "", "exclude_phrases": "",
    "levels": ["Internship", "Entry level", "Associate", "Mid-Senior level", "Not Applicable"],
    "skip_german": False, "skip_no_sponsorship": False,
}

# "(Senior) Engineer" and "all levels" are open to any level, so they don't count as senior titles
OPEN_ENDED = re.compile(
    r"\(\s*(jr\.?|junior|senior|sr\.?|mid)\s*-?\s*(/\s*(junior|senior|mid|lead)\s*)?-?\)|"
    r"all[\s-]+seniorit\w*|all[\s-]+levels?|any[\s-]+level|(junior|mid)\s*(/|to|-|–)\s*senior", re.I)
GERMAN_REQ = re.compile(
    r"(fluent|fluency|proficien\w*|excellent|very good|good|strong|business[- ]level|native|working knowledge|command|skills)"
    r"\s+(\w+\s+){0,3}(in\s+)?German|German\s*(\(|-|–)?\s*(C1|C2|B2|B1|fluent|native|business|mother)|"
    r"German (and|&|as well as) English|English (and|&) German|German language (skills|proficiency)|Deutschkenntnisse|"
    r"speak(s|ing)? German|German speaker|German is (required|mandatory|a must)|Deutsch (C1|B2|fließend)|both German and English",
    re.I)
GERMAN_REQ_DE = re.compile(
    r"(fließend\w*|sehr gut\w*|gut\w*|verhandlungssicher\w*|exzellent\w*|ausgezeichnet\w*|sicher\w*|"
    r"fundiert\w*|solide\w*|mindestens)\s+(\w+\s+){0,3}Deutsch|"
    r"Deutsch\w*\s*(\(|-|–|:)?\s*(auf\s+)?(C1|C2|B2|fließend|verhandlungssicher|Muttersprach\w*|in Wort und Schrift|"
    r"(auf\s+)?(sehr\s+)?gute[mn]?\s+Niveau)|Deutsch(-|kenntnisse)?\s*(und|&|sowie)\s+Englisch|"
    r"Englisch\w*\s*(und|&|sowie)\s+Deutsch|deutsche[rn]? Sprache (in Wort und Schrift|ist erforderlich|voraus)|"
    r"(auf|in) Deutsch und Englisch|Deutsch als Arbeitssprache|deutschsprachig\w*", re.I)
GERMAN_OPTIONAL = re.compile(
    r"German[^.]{0,60}(plus|advantage|nice to have|bonus|beneficial|asset)|(plus|advantage|nice to have|bonus)[^.]{0,40}German|"
    r"Deutsch\w*[^.]{0,25}(von Vorteil|wünschenswert|ein Plus|nice to have|hilfreich|kein Muss|"
    r"nicht (zwingend )?erforderlich|keine Voraussetzung)", re.I)
GERMAN_STRONG = re.compile(
    r"(fluent|C1|C2|B2|business|native)[^.]{0,20}German|German[^.]{0,20}(C1|C2|B2|fluent|native)|"
    r"(fließend\w*|sehr gute\w*|sicher\w*|verhandlungssicher\w*) (\w+ ){0,2}Deutsch|Deutsch\w*\s*\(?(C1|C2|Muttersprach)", re.I)
ENGLISH_OK = re.compile(
    r"Arbeitssprache\s+(ist\s+)?Englisch|Englisch als Arbeitssprache|English is (our|the) (working|company|team) language|"
    r"working language is English|Deutsch\w*\s+(ist\s+)?(nicht (zwingend )?erforderlich|keine Voraussetzung|kein Muss)|"
    r"German (is )?(not required|not a requirement|not necessary)", re.I)
NO_SPONSORSHIP = re.compile(
    r"EU work authori[sz]ation|(no|without) visa sponsorship|(cannot|can't|can not|unable to|do not|don't|not able to)"
    r"\s+(offer|provide|support)?\s*(visa|relocation or visa|sponsor)|must (already )?(have|hold) (the )?(right to work|"
    r"valid work permit|EU citizenship)|EU citizen(ship)? (is )?(required|mandatory)|only (EU|EEA) citizens|"
    r"citizens? of the (EU|EEA) only|keine Visa", re.I)
STOPWORDS = set("""about above after again also among and any are based been before being below between both but can
company could data did does each experience for from further good had has have help here how ideal into its join just
like looking make more most must new not now off offer one only other our out over own part people role same should
skills some strong such team than that the their them then there these they this those through time under until use
using very want was we well were what when where which while who why will with work working would years you your""".split())


def lines(text):
    return [l.strip() for l in str(text or "").splitlines() if l.strip()]


def clean(s):
    return html.unescape(re.sub(r"\s+", " ", s)).strip()


def word_pattern(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I) if words else None


def requires_german(text):
    """True when a posting clearly asks for working German (an optional 'German is a plus' doesn't count)."""
    if ENGLISH_OK.search(text):
        return False
    strong = GERMAN_STRONG.search(text)
    return bool((GERMAN_REQ.search(text) or GERMAN_REQ_DE.search(text)) and (strong or not GERMAN_OPTIONAL.search(text))
                and strong)


def min_years(text):
    """Lowest 'N years' that is about experience, not phrases like 'founded 10 years ago'."""
    found = [int(m.group(1)) for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:-\s*\d+\s*)?(?:years|yrs|Jahre)", text, re.I)
             if int(m.group(1)) < 15 and re.search(r"experience|erfahrung|track record|professional|background",
                                                   text[max(0, m.start() - 60):m.end() + 60], re.I)]
    return min(found) if found else None


def title_rejected(title, cfg):
    include = word_pattern(lines(cfg.get("include_title")))
    if include and not include.search(title):
        return "title doesn't match"
    exclude = word_pattern(lines(cfg.get("exclude_title")))
    if exclude and exclude.search(OPEN_ENDED.sub(" ", title)):
        return "title excluded"
    return ""


def company_rejected(company, cfg):
    pattern = word_pattern(lines(cfg.get("exclude_companies")))
    return "company excluded" if pattern and pattern.search(company) else ""


def posting_rejected(job, cfg):
    """Why this posting should be dropped, or '' to keep it."""
    text = job.get("description", "")
    if not text:
        return "no description"
    if job.get("level") and job["level"] not in cfg.get("levels", LEVELS):
        return f"level: {job['level']}"
    limit = str(cfg.get("max_years") or "").strip()
    years = min_years(text)
    if limit.isdigit() and years is not None and years > int(limit):
        return f"asks for {years}+ years"
    if cfg.get("skip_german") and requires_german(text):
        return "German required"
    if cfg.get("skip_no_sponsorship") and NO_SPONSORSHIP.search(text):
        return "no visa sponsorship"
    lowered = text.lower()
    for phrase in lines(cfg.get("exclude_phrases")):
        if phrase.lower() in lowered:
            return f"contains: {phrase}"
    return ""


def norm_key(job):
    """Company + title, so the same posting in two cities or two searches is only kept once."""
    company = re.sub(r"[^a-z0-9 ]", "", job.get("company", "").lower()).split()
    title = re.sub(r"\((m|w|d|f|x|all|gn|div)[^)]*\)|[^a-z0-9]", "", job.get("title", "").lower())
    return (company[0] if company else "", title)


def quick_fit(job, cv_text):
    """Rough 1-4 guess from words shared with the CV, so a long list can be sorted before generating.
    Never 5: only a real generation can say a job is a strong match."""
    def words(s):
        return {w for w in re.findall(r"[a-z][a-z0-9+#.-]{2,}", s.lower()) if w not in STOPWORDS}
    posting, cv = words(job.get("description", "")), words(cv_text)
    if not posting or not cv:
        return None
    score = len(posting & cv) / len(cv) ** 0.5  # the square root stops long CVs from always scoring high
    return 4 if score >= .95 else 3 if score >= .65 else 2 if score >= .4 else 1


class Linkedin:
    def __init__(self, cancelled=lambda: False):
        self.last = 0.0
        self.cancelled = cancelled
        self.requests = self.throttled = 0

    def get(self, url):
        for attempt in range(4):
            if self.cancelled():
                return ""
            wait = PACE_SECONDS - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.requests += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                    return r.read().decode("utf-8", "ignore")
            except urllib.error.HTTPError as e:
                if e.code == 429:  # told to slow down: wait, and give up after the last try
                    self.throttled += 1
                    time.sleep(60 * (attempt + 1))
                    continue
                if e.code in (400, 404):
                    return ""
                time.sleep(3 + attempt * 3)
            except OSError:
                time.sleep(3 + attempt * 3)
        return ""

    def search(self, query, location, days, max_pages):
        found, seen = {}, set()
        for start in range(0, max_pages * 10, 10):
            page = self.get(f"{BASE}/seeMoreJobPostings/search?keywords={urllib.parse.quote(query)}"
                            f"&location={urllib.parse.quote(location)}&sortBy=DD&f_TPR=r{days * 86400}&start={start}")
            ids = set()
            for card in page.split("<li>")[1:]:
                m = re.search(r"jobPosting:(\d+)", card)
                if not m:
                    continue
                ids.add(m[1])

                def field(pattern):
                    hit = re.search(pattern, card, re.S)
                    return clean(hit[1]) if hit else ""
                found.setdefault(m[1], {
                    "linkedin_id": m[1], "title": field(r'base-search-card__title">(.*?)</h3>'),
                    "company": clean(re.sub(r"<[^>]+>", " ", field(r'base-search-card__subtitle"[^>]*>(.*?)</h4>'))),
                    "location": field(r'job-search-card__location">(.*?)</span>'),
                    "posted": field(r'datetime="(.*?)"')})
            if not ids or ids <= seen:  # LinkedIn repeats the last page instead of ending
                break
            seen |= ids
        return found

    def details(self, job):
        page = self.get(f"{BASE}/jobPosting/{job['linkedin_id']}")
        m = re.search(r"show-more-less-html__markup[^>]*>(.*?)</div>", page, re.S)
        job["description"] = clean(re.sub(r"<[^>]+>", " ", m[1])) if m else ""
        criteria = dict(re.findall(r'description__job-criteria-subheader">\s*(.*?)\s*</h3>\s*<span[^>]*>\s*(.*?)\s*</span>',
                                   page, re.S))
        job["level"] = clean(criteria.get("Seniority level", ""))
        job["url"] = f"https://www.linkedin.com/jobs/view/{job['linkedin_id']}/"
        return job


def run(cfg, seen_ids, seen_keys, cv_text, on_progress, on_batch, cancelled=lambda: False):
    """Runs every query in every location. on_batch(kept, rejected) saves after each search, so stopping
    early keeps what was found. rejected is {linkedin_id: reason}, remembered so it's never fetched again."""
    client = Linkedin(cancelled)
    tasks = [(q, l) for l in lines(cfg.get("locations")) for q in lines(cfg.get("queries"))]
    days, pages = max(int(cfg.get("days") or 1), 1), min(max(int(cfg.get("max_pages") or 10), 1), 40)
    total_new = 0
    for n, (query, location) in enumerate(tasks, 1):
        if cancelled():
            break
        on_progress(f"{query} in {location}", n, len(tasks), total_new, client.throttled)
        kept, rejected = [], {}
        for job_id, job in client.search(query, location, days, pages).items():
            if cancelled():
                break
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            reason = title_rejected(job["title"], cfg) or company_rejected(job["company"], cfg)
            if not reason and norm_key(job) in seen_keys:
                reason = "duplicate"
            if not reason:
                client.details(job)
                reason = posting_rejected(job, cfg)
            if reason:
                rejected[job_id] = reason
                continue
            seen_keys.add(norm_key(job))
            job["fit"] = quick_fit(job, cv_text)
            kept.append(job)
        on_batch(kept, rejected)
        total_new += len(kept)
    return total_new, client.throttled
