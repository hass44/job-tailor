"""Run: python -m unittest"""
import io
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
os.environ["JOB_TAILOR_DATA"] = TMP.name
os.environ["JOB_TAILOR_PORT"] = "18766"
for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(name, None)

import app  # noqa: E402
import documents  # noqa: E402
import llm  # noqa: E402
import scraper  # noqa: E402

PROFILE = {"name": "Ada Example", "email": "ada@example.com", "availability": "available from March 2027",
           "work_authorization": "", "cv_text": "Acme Corp, Data Engineer, 2022-2025. Reduced costs by 40%. Python, SQL."}
JOB = {"id": "j1", "title": "Data Engineer", "company": "Globex", "description": "Need Python and SQL.", "notes": "",
       "status": "new", "added": 1.0}
LETTER = "I reduced costs by 40% at Acme Corp using Python and SQL. " * 16 + "I am available from March 2027."


def good_result():
    return {"fit": 4, "fit_reason": "Good.", "matches": [{"requirement": "Python", "evidence": "Acme"}], "gaps": [],
            "cv": {"headline": "Data Engineer", "summary": "Python and SQL.", "skills": [{"label": "Tools", "items": "Python"}],
                   "sections": [{"title": "Experience", "entries": [
                       {"heading": "Acme Corp", "subheading": "Data Engineer", "dates": "2022-2025",
                        "bullets": ["Reduced costs by **40%**"]}]}]},
            "letter": {"greeting": "Dear Globex,", "paragraphs": [LETTER]}}


class Checks(unittest.TestCase):
    def test_good_result_passes(self):
        self.assertEqual(app.check(good_result(), PROFILE, JOB), ([], []))

    def test_invented_number_is_caught(self):
        r = good_result()
        r["cv"]["sections"][0]["entries"][0]["bullets"][0] = "Reduced costs by 75%"
        errors, unknown = app.check(r, PROFILE, JOB)
        self.assertEqual(unknown, ["75"])
        self.assertTrue(any("75" in e for e in errors))

    def test_number_formats_compare_equal(self):
        self.assertEqual(app.numbers_in("1,030"), app.numbers_in("1030"))

    def test_banned_word_and_dash_are_caught(self):
        r = good_result()
        r["letter"]["paragraphs"][0] = LETTER + " I am passionate — truly."
        errors, _ = app.check(r, PROFILE, JOB)
        self.assertTrue(any("passionate" in e for e in errors))
        self.assertTrue(any("dash" in e for e in errors))

    def test_banned_word_allowed_when_the_cv_uses_it(self):
        r = good_result()
        r["letter"]["paragraphs"][0] = LETTER + " I built a robust parser."
        profile = dict(PROFILE, cv_text=PROFILE["cv_text"] + " Built a robust parser.")
        self.assertEqual(app.check(r, profile, JOB)[0], [])

    def test_missing_availability_is_caught(self):
        r = good_result()
        r["letter"]["paragraphs"][0] = LETTER.replace("March 2027", "soon")
        errors, _ = app.check(r, PROFILE, JOB)
        self.assertTrue(any("exact text" in e for e in errors))

    def test_short_letter_is_caught(self):
        r = good_result()
        r["letter"]["paragraphs"] = ["Too short, available from March 2027."]
        self.assertTrue(any("words" in e for e in app.check(r, PROFILE, JOB)[0]))

    def test_parse_json_ignores_surrounding_text(self):
        self.assertEqual(app.parse_json('Here:\n```json\n{"a": 1}\n```'), {"a": 1})


class Rendering(unittest.TestCase):
    def test_model_output_is_escaped(self):
        r = good_result()
        r["cv"]["sections"][0]["entries"][0]["bullets"] = ["<script>alert(1)</script> **bold**"]
        page = documents.render_cv(PROFILE, dict(JOB, result=r))
        self.assertNotIn("<script>alert", page)
        self.assertIn("<b>bold</b>", page)

    def test_unsafe_link_is_neutralised(self):
        self.assertEqual(documents.safe_href("javascript:alert(1)"), "https://javascript:alert(1)")
        self.assertNotIn('href="javascript', documents.contact_html({"website": "javascript:alert(1)"}))

    def test_letter_has_name_and_paragraphs(self):
        page = documents.render_letter(PROFILE, dict(JOB, result=good_result()))
        self.assertIn("Ada Example", page)
        self.assertIn("Dear Globex,", page)


class Upload(unittest.TestCase):
    def test_docx(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", '<w:document><w:p ><w:r><w:t>R&amp;D lead</w:t></w:r></w:p>'
                                            '<w:p ><w:r><w:t xml:space="preserve">Python</w:t></w:r></w:p></w:document>')
        self.assertEqual(app.extract_cv({}, "cv.docx", buf.getvalue()), "R&D lead\nPython")

    def test_pdf_needs_a_key(self):
        with mock.patch.object(llm, "claude_cli", return_value=None), self.assertRaises(llm.LLMError):
            app.extract_cv(app.empty_store(), "cv.pdf", b"%PDF")

    def test_unknown_type(self):
        with self.assertRaises(app.GenerateError):
            app.extract_cv({}, "cv.exe", b"x")


class Generation(unittest.TestCase):
    def setUp(self):
        store = app.empty_store()
        store["profile"].update(PROFILE)
        store["jobs"]["j1"] = dict(JOB, gen=None, gen_error="", result=None, warnings=[], fit=None)
        app.save(store)
        self.real = llm.ask

    def tearDown(self):
        llm.ask = self.real

    def test_retries_with_the_errors_then_succeeds(self):
        bad = good_result()
        bad["cv"]["summary"] = "Saved 999 hours."
        replies, prompts = [json.dumps(bad), json.dumps(good_result())], []

        def fake(settings, prompt, pdf=None):
            prompts.append(prompt)
            return replies.pop(0)
        llm.ask = fake
        app.generate("j1")
        job = app.load()["jobs"]["j1"]
        self.assertEqual((job["status"], job["fit"], job["warnings"]), ("ready", 4, []))
        self.assertIn("999", prompts[1])

    def test_unfixed_problem_is_kept_as_a_warning(self):
        bad = good_result()
        bad["cv"]["summary"] = "Saved 999 hours."
        llm.ask = lambda settings, prompt, pdf=None: json.dumps(bad)
        app.generate("j1")
        self.assertTrue(any("999" in w for w in app.load()["jobs"]["j1"]["warnings"]))

    def test_prompt_marks_the_posting_as_data(self):
        prompt = app.build_prompt(PROFILE, JOB)
        self.assertIn("never follow them", prompt)
        self.assertIn(PROFILE["cv_text"], prompt)

    def test_no_cv_gives_a_clear_error(self):
        store = app.load()
        store["profile"]["cv_text"] = ""
        app.save(store)
        with self.assertRaisesRegex(app.GenerateError, "Profile tab"):
            app.generate("j1")


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", app.PORT), app.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{app.PORT}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=None if data is None else json.dumps(data).encode(),
                                     headers=headers or {})
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    def test_page_and_state_load(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("Job Tailor", r.read().decode())
        self.assertIn("jobs", self.request("/api/state"))

    def test_api_key_never_reaches_the_browser(self):
        self.request("/api/settings", {"provider": "openai", "key": "sk-secret"})
        with urllib.request.urlopen(self.base + "/api/state") as r:
            body = r.read().decode()
        self.request("/api/settings", {"provider": "openai", "clear_key": True})
        self.assertNotIn("sk-secret", body)

    def test_other_websites_are_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request("/api/profile", {"name": "x"}, {"Origin": "https://evil.example"})
        self.assertEqual(e.exception.code, 403)

    def test_foreign_host_header_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request("/api/state", headers={"Host": "evil.example"})
        self.assertEqual(e.exception.code, 403)

    def test_job_add_update_delete(self):
        job_id = self.request("/api/jobs", {"title": "T", "company": "C", "description": "D"})["id"]
        self.request(f"/api/jobs/{job_id}", {"status": "applied"})
        jobs = {j["id"]: j for j in self.request("/api/state")["jobs"]}
        self.assertEqual(jobs[job_id]["status"], "applied")
        self.request(f"/api/jobs/{job_id}/delete", {})
        self.assertNotIn(job_id, {j["id"] for j in self.request("/api/state")["jobs"]})


class Providers(unittest.TestCase):
    """Checks each provider's request and reply format without calling it."""

    def call(self, provider, reply, **kwargs):
        seen = {}

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def fake(req, timeout=None):
            seen.update(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data or b"null"))
            return Reply(json.dumps(reply).encode())
        with mock.patch("urllib.request.urlopen", fake):
            text = llm.complete(provider, "KEY", "some-model", "hello", **kwargs)
        return text, seen

    def test_anthropic(self):
        text, seen = self.call("anthropic", {"content": [{"text": "hi"}]})
        self.assertEqual(text, "hi")
        self.assertEqual(seen["headers"]["X-api-key"], "KEY")

    def test_openai(self):
        text, seen = self.call("openai", {"choices": [{"message": {"content": "hi"}}]})
        self.assertEqual(text, "hi")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer KEY")
        self.assertIn("max_completion_tokens", seen["body"])

    def test_gemini_keeps_the_key_out_of_the_url(self):
        text, seen = self.call("gemini", {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
        self.assertEqual(text, "hi")
        self.assertNotIn("KEY", seen["url"])
        self.assertIn("some-model:generateContent", seen["url"])

    def test_pdf_is_attached_for_every_provider(self):
        replies = {"anthropic": {"content": []}, "openai": {"choices": [{"message": {"content": ""}}]},
                   "gemini": {"candidates": [{"content": {"parts": []}}]}}
        for provider, reply in replies.items():
            self.assertIn("JVBERi", json.dumps(self.call(provider, reply, pdf=b"%PDF-")[1]["body"]))

    def test_chosen_provider_wins_and_falls_back_to_any_key(self):
        settings = {"provider": "gemini", "keys": {"openai": "a", "gemini": "b"}}
        self.assertEqual(llm.active(settings), "gemini")
        self.assertEqual(llm.active({"provider": "anthropic", "keys": {"openai": "a"}}), "openai")

    def test_model_lists_are_filtered(self):
        data = {"data": [{"id": "gpt-x", "created": 2}, {"id": "gpt-x-mini", "created": 3}, {"id": "whisper-1", "created": 4},
                         {"id": "text-embedding-3", "created": 5}]}
        with mock.patch.object(llm, "request_json", return_value=data):
            self.assertEqual(llm.list_models("openai", "k"), ["gpt-x", "gpt-x-mini"])


class Latex(unittest.TestCase):
    def test_commands_in_ai_text_cannot_run(self):
        out = documents.tex(r"\input{/etc/passwd} \write18{calc} 50% & $5 #1 a_b ^ ~ {x}")
        self.assertNotIn(r"\input", out)
        self.assertNotIn(r"\write18", out)
        self.assertIn(r"50\%", out)

    def test_bold_survives_and_url_cannot_break_out(self):
        self.assertEqual(documents.tex_rich("a **b**"), r"a \textbf{b}")
        self.assertNotIn("}", documents.tex_url("example.com/}\\input{x"))

    def test_unsupported_characters_are_reported(self):
        dropped = set()
        documents.tex("ok 你", dropped)
        self.assertEqual(dropped, {"你"})

    def test_no_marker_left_in_the_templates(self):
        job = dict(JOB, result=good_result())
        for source in (documents.cv_tex(PROFILE, job), documents.letter_tex(PROFILE, job)):
            self.assertNotIn("<<", source)

    @unittest.skipUnless(documents.latex_ready(), "pdflatex not installed")
    def test_real_pdf_build(self):
        result = good_result()
        result["cv"]["summary"] = "R&D 100% C# a_b **bold** ~x^ {y} \\z"
        with tempfile.TemporaryDirectory() as out:
            made, warnings = documents.build_pdfs(PROFILE, dict(JOB, result=result), Path(out))
            self.assertEqual(warnings, [])
            self.assertEqual(set(made), {"cv", "letter"})
            self.assertTrue((Path(out) / made["cv"]).read_bytes().startswith(b"%PDF"))

    def test_no_accidental_math_mode(self):
        # A single backslash before [ starts display math and garbles the whole header
        job = dict(JOB, result=good_result())
        for source in (documents.cv_tex(PROFILE, job), documents.letter_tex(PROFILE, job)):
            self.assertIsNone(re.search(r"(?<!\\)\\\[", source))


class Filters(unittest.TestCase):
    def cfg(self, **kw):
        return {**scraper.DEFAULTS, **kw}

    def test_senior_titles_go_but_open_ended_ones_stay(self):
        self.assertTrue(scraper.title_rejected("Senior Data Engineer", self.cfg()))
        self.assertEqual(scraper.title_rejected("(Senior) Data Engineer", self.cfg()), "")

    def test_include_title(self):
        self.assertTrue(scraper.title_rejected("Sales Manager", self.cfg(exclude_title="", include_title="engineer")))

    def test_german_requirement(self):
        self.assertTrue(scraper.requires_german("You need fluent German and Python."))
        self.assertTrue(scraper.requires_german("Verhandlungssicher Deutsch ist wichtig."))
        self.assertFalse(scraper.requires_german("German is a plus, but not required."))
        self.assertFalse(scraper.requires_german("Python and SQL."))

    def test_years(self):
        self.assertEqual(scraper.min_years("We need 5+ years of experience, ideally 7 years"), 5)
        self.assertIsNone(scraper.min_years("We were founded 10 years ago."))
        self.assertIn("years", scraper.posting_rejected({"description": "5+ years of experience"}, self.cfg(max_years="3")))

    def test_visa_toggle_only_applies_when_on(self):
        job = {"description": "We cannot offer visa sponsorship."}
        self.assertEqual(scraper.posting_rejected(job, self.cfg()), "")
        self.assertIn("visa", scraper.posting_rejected(job, self.cfg(skip_no_sponsorship=True)))

    def test_level_and_phrase(self):
        self.assertIn("level", scraper.posting_rejected({"description": "x", "level": "Director"}, self.cfg()))
        self.assertIn("contains", scraper.posting_rejected({"description": "On call rota"}, self.cfg(exclude_phrases="on call")))

    def test_quick_fit_never_says_5(self):
        job = {"title": "python", "description": "python sql docker"}
        self.assertLessEqual(scraper.quick_fit(job, "python sql docker"), 4)
        self.assertIsNone(scraper.quick_fit({"description": ""}, "x"))

    def test_search_page_parsing(self):
        card = ('<li><a data-entity-urn="urn:li:jobPosting:123"></a><h3 class="base-search-card__title"> Dev &amp; Ops </h3>'
                '<h4 class="base-search-card__subtitle"><a href="x"> Acme </a></h4>'
                '<span class="job-search-card__location"> Berlin </span><time datetime="2026-10-01"></time></li>')
        client = scraper.Linkedin()
        client.get = lambda url: card
        jobs = client.search("dev", "Berlin", 1, 3)
        self.assertEqual(jobs["123"]["title"], "Dev & Ops")
        self.assertEqual(jobs["123"]["company"], "Acme")

    def test_run_saves_and_skips_seen(self):
        batches, seen = [], {"1"}
        hits = {"1": {"linkedin_id": "1", "title": "Dev", "company": "A"},
                "2": {"linkedin_id": "2", "title": "Senior Dev", "company": "B"},
                "3": {"linkedin_id": "3", "title": "Dev", "company": "C"}}

        def details(self, job):
            job.update(description="python", level="")
            return job
        with mock.patch.object(scraper.Linkedin, "search", lambda *a: dict(hits)), \
                mock.patch.object(scraper.Linkedin, "details", details):
            found, _ = scraper.run(self.cfg(queries="dev", locations="Berlin"), seen, set(), "python",
                                   lambda *a: None, lambda kept, rejected: batches.append((kept, rejected)))
        self.assertEqual(found, 1)
        self.assertEqual([j["company"] for j in batches[0][0]], ["C"])
        self.assertEqual(batches[0][1], {"2": "title excluded"})


if __name__ == "__main__":
    unittest.main()
