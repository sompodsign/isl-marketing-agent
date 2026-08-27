# EvaBoot Job Application — 5-Minute Agentic Project Walkthrough

## Recommendation

Use **ISL Marketing Agent** as the main project and stay with it for almost the entire five minutes.

It fits the prompt better than SpeakingDude because it is clearly an agent: it retrieves knowledge, selects context, generates content, evaluates its output through deterministic checks, retries with feedback, requests human approval, schedules work, publishes externally, and records events.

SpeakingDude is an impressive AI application, but explaining both architectures in five minutes would feel rushed. Mention SpeakingDude briefly near the end as the larger project where the same lessons are being applied.

## Five-Minute Recording Script

### 0:00–0:30 — Show the Running Dashboard

> Hi, I’m Shampad. This is the InariSoftLabs Marketing Agent, a Python application I built to generate and publish Facebook content for several software products.
>
> I built it because using a general chatbot for marketing created three problems: it could invent product features, produce Bangla that sounded translated or overly corporate, and repeat similar posts. I wanted an agentic workflow that could operate independently, but only inside clearly defined boundaries.

### 0:30–1:15 — Show the Project Tree, Then `app/main.py`

> The architecture is intentionally simple.
>
> There is a browser dashboard, a FastAPI backend, SQLite for knowledge and workflow state, several supported model providers, and Facebook’s Graph API as the external action.
>
> `main.py` handles the HTTP endpoints, authentication, scheduling, and application lifecycle. `services.py` contains the generation workflow, retrieval, validation, provider integrations, and publishing logic.
>
> I avoided introducing a large agent framework because this workflow did not need one. Keeping the orchestration in normal Python makes every state transition inspectable and testable.

Briefly point to:

- `app/main.py`
- `app/services.py`
- `app/database.py`
- `tests/test_app.py`

### 1:15–2:25 — Show `generate_post()` in `app/services.py`

> This is the core agent loop.
>
> First, the agent retrieves only reviewed product knowledge using SQLite full-text search. It also receives reviewed descriptions of product screenshots. The selected assets establish which product the agent is allowed to discuss, so it cannot accidentally combine claims from different products.
>
> The model returns structured data containing the caption, cited fact IDs, selected image, hashtags, and a confidence level.
>
> But I do not trust the model simply because it returned valid JSON. Python checks whether the answer contains planning text, unsupported fact IDs, the wrong number of hashtags, banned marketing clichés, an invalid image, or an incorrect length.
>
> It also compares the caption with recent posts using Jaccard similarity. If a check fails, the agent receives a targeted explanation—such as “this draft is too similar” or “you cited an unavailable fact”—and tries again, up to four times.
>
> The key design decision is that the model proposes an action, but deterministic code decides whether that action is acceptable.

Scroll through the validation conditions and the retry `variation_note` messages.

### 2:25–3:15 — Return to the Dashboard and Show a Draft/Event History

> I observe the agent through persisted workflow state and an event history.
>
> For every post, I can inspect the generated caption, selected visual, confidence, cited facts, current status, publishing errors, and Facebook post ID.
>
> The default mode is human approval. I can edit a draft before publication, while automatic publishing remains an explicit configuration choice.
>
> For quality, I use three layers: deterministic validation, focused automated tests, and human review—especially for natural Bangladeshi Bangla. For example, this test deliberately makes the first model response too similar to an existing post and verifies that the agent rejects it and generates a different draft.

Show `test_generate_post_retries_when_draft_is_too_similar`.

Run these focused tests:

```bash
pytest -q tests/test_app.py::test_generation_uses_human_example_and_validates_facts tests/test_app.py::test_generate_post_retries_when_draft_is_too_similar tests/test_app.py::test_unreviewed_imports_are_not_retrieved tests/test_app.py::test_publish_rejects_post_without_an_image
```

Expected result:

```text
4 passed
```

### 3:15–4:10 — Show Publishing and Scheduler Code

> One of the hardest parts was realizing that content quality was only half the problem. Publishing is an external side effect, so retries can accidentally create duplicate posts.
>
> I made publishing idempotent using explicit states such as draft, publishing, published, and failed. If a request is repeated after publication, it returns the existing result. The application also detects publishing jobs interrupted during a restart and moves them into a reviewable failed state.
>
> Another challenge was natural Bangla. Longer prompts alone did not solve it. I needed locally appropriate writing examples, explicit style boundaries, grounding in reviewed facts, and post-generation validation.
>
> Supporting multiple model providers was also interesting because OpenAI, Gemini, and DeepSeek return different response structures. I normalize those differences behind one writer function while keeping the rest of the agent workflow provider-independent.

### 4:10–5:00 — Briefly Show SpeakingDude, Then Return to the Marketing Agent

> I am applying the same lessons in SpeakingDude, a larger Django-based language-learning product. It has an AI facade, interchangeable provider strategies, structured speaking feedback, caching, prompt versions, and deterministic scoring around model output.
>
> The main thing I have learned from both projects is that a dependable agent is not just a good prompt. It is a controlled loop around a probabilistic component: constrained context, structured output, validation, retry policy, state, observability, tests, and human escalation.
>
> What I am still exploring is better semantic evaluation. Jaccard similarity catches repeated vocabulary, but it does not fully understand whether two posts communicate the same idea. I also want stronger automated evaluation for culturally natural Bangla and better tracking of model latency, cost, and human edit rates.
>
> That is the part of agentic engineering I find most interesting: turning useful model behavior into a workflow people can actually trust.

## Tabs to Prepare Before Recording

> [!CAUTION]
> Keep `.env` completely closed during the recording because it may contain credentials.

Open these in order:

1. The running marketing-agent dashboard.
2. `app/main.py` around the scheduler (`run_schedule`).
3. `app/services.py` around the generation loop (`generate_post`).
4. `tests/test_app.py` around `test_generate_post_retries_when_draft_is_too_similar`.
5. `app/services.py` around idempotent publishing (`publish_post`).
6. SpeakingDude’s `sd-backend/src/apps/ai/` folder for the final brief reference.

## Final Recording Checklist

- Start on the dashboard rather than this README.
- Close `.env` before screen sharing.
- Increase the editor and terminal font sizes.
- Hide terminal history that might contain secrets.
- Disable desktop and browser notifications.
- Prepare the exact files and line positions in advance.
- Use the focused test command above instead of the entire test suite.
- Rehearse the transitions once while timing yourself.
- Speak naturally; use the script as a guide rather than reading every sentence rigidly.

## LinkedIn Channel

The agent publishes to two channels. Facebook posts target product users (often Bangla); LinkedIn posts target potential clients on the InariSoftLabs Company Page to attract custom software development work.

- Every post stores a `channel` (`facebook` or `linkedin`). LinkedIn drafts are always generated in professional English with a service-focused brief: one concrete workflow from a delivered product as proof, grounded in verified knowledge plus company-level brand facts, with the same deterministic validation (planning markers, fact IDs, hashtag count, banned phrases, 80–220 words, Jaccard similarity) as Facebook.
- Publishing is per channel and idempotent: `publish_post` claims the row, uploads the image through LinkedIn's register/PUT flow (`/rest/uploads` → binary PUT → `/rest/posts`), and stores `linkedin_post_id`. Video is Facebook-only; LinkedIn requires one product screenshot.
- When LinkedIn is configured, each due schedule slot also creates a LinkedIn companion post (best-effort; a LinkedIn failure never fails the Facebook slot).

Setup:

1. Create a LinkedIn app, request the **Community Management API** product, and add `http://localhost:8080/linkedin/callback` as a redirect URL.
2. `python scripts/linkedin_token.py authorize` → approve in the browser as the Company Page admin → `python scripts/linkedin_token.py exchange --code <CODE>`.
3. `python scripts/linkedin_token.py whoami` lists the Company Pages the token can post to; set `LINKEDIN_AUTHOR_URN` to the `urn:li:organization:<PAGE_ID>` value.
4. Access tokens expire after ~60 days: `python scripts/linkedin_token.py refresh`.
