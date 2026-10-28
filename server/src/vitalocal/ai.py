"""AI gateway: OpenAI-compatible chat API (Ollama, LM Studio, llama.cpp, vLLM, or a hosted provider).

The model never queries the database. VitaLocal builds a deterministic fact sheet (see
ai_facts) and the model only interprets it. The fact sheet is stored with every summary so
each statement can be traced back.

AI is optional: without a configured model every AI feature is hidden (`available`). A model
outside the local network is "external"; it gets only the features and data the person shared
with it (`share`): journal text and lab reports stay at home by default.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from urllib.parse import urlparse

import httpx
from sqlalchemy import Connection, text

from vitalocal import ai_facts, vault
from vitalocal.config import get_settings

SETTING_KEY = "ai"
# What an external model may be used for / may see; a local model gets everything
FEATURES = ("summary", "coach", "labs", "journal")
DEFAULT_SHARE = {"summary": True, "coach": True, "labs": False, "journal": False}
LOCAL_SUFFIXES = (".local", ".lan", ".arpa", ".home", ".internal", ".localhost", ".localdomain", ".test")


def is_external(base_url: str) -> bool:
    """Whether the endpoint lies outside the local network (hosted provider)."""
    host = (urlparse(base_url).hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or "." not in host or host.endswith(LOCAL_SUFFIXES):
        return False  # single-label names are LAN / Docker service names
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local)


@dataclass
class AiSettings:
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    temperature: float = 0.3
    timeout_s: int = 180
    focus: str = ""  # personal focus appended to the prompt, e.g. "muscle gain, better sleep"
    vision: bool = False  # the model can read images (needed for lab reports)
    share: dict = field(default_factory=lambda: dict(DEFAULT_SHARE))

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.model)

    @property
    def external(self) -> bool:
        return bool(self.base_url) and is_external(self.base_url)

    def shares(self, feature: str) -> bool:
        """Whether this feature / data may go to the model (always for a local one)."""
        return not self.external or bool({**DEFAULT_SHARE, **(self.share or {})}.get(feature))

    def public(self) -> dict:
        d = asdict(self)
        d["has_api_key"] = bool(d.pop("api_key"))
        d["share"] = {**DEFAULT_SHARE, **(self.share or {})}
        d["configured"], d["external"] = self.configured, self.external
        return d


def available(s: AiSettings, feature: str) -> bool:
    """Whether an AI feature (summary, coach, labs) can be offered at all."""
    if not s.configured or not s.shares(feature):
        return False
    return s.vision if feature == "labs" else True


def unavailable_reason(s: AiSettings, feature: str) -> str:
    if not s.configured:
        return "no AI model is set up (WebUI → Settings → AI)"
    if not s.shares(feature):
        return f"not shared with the external AI model ({feature}; WebUI → Settings → AI)"
    return "the AI model cannot read images (WebUI → Settings → AI)"


def load_settings(conn: Connection) -> AiSettings:
    env = get_settings()
    s = AiSettings(
        enabled=bool(env.ai_base_url and env.ai_model),
        base_url=env.ai_base_url or "",
        model=env.ai_model or "",
        api_key=env.ai_api_key.get_secret_value() if env.ai_api_key else "",
    )
    row = conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": SETTING_KEY}).first()
    if row:
        for k, v in row.value.items():
            if hasattr(s, k) and v is not None:
                setattr(s, k, v)
        if "vision" not in row.value:
            s.vision = True  # set up before the switch existed: lab reports were read with this model
        stored = row.value.get("api_key") or ""
        s.api_key = vault.decrypt(stored)
        if stored and not vault.is_encrypted(stored):
            save_settings(conn, s)  # key from before encryption: encrypt it now
    return s


def save_settings(conn: Connection, s: AiSettings) -> None:
    d = asdict(s)
    d["api_key"] = vault.encrypt(s.api_key)
    conn.execute(
        text("""INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"""),
        {"k": SETTING_KEY, "v": json.dumps(d)},
    )


class AiError(Exception):
    pass


def _client(s: AiSettings) -> httpx.Client:
    headers = {"Authorization": f"Bearer {s.api_key}"} if s.api_key else {}
    return httpx.Client(
        base_url=s.base_url.rstrip("/"), headers=headers, timeout=s.timeout_s, transport=TRANSPORT
    )


# Tests replace this with an httpx.MockTransport.
TRANSPORT: httpx.BaseTransport | None = None


def list_models(s: AiSettings) -> list[str]:
    try:
        with _client(s) as c:
            r = c.get("/models", timeout=10)
            r.raise_for_status()
            return sorted(m.get("id", "") for m in r.json().get("data", []))
    except (httpx.HTTPError, ValueError) as e:
        raise AiError(f"{type(e).__name__}: {e}") from e


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _image_type(b: bytes) -> str:
    return "image/png" if b.startswith(b"\x89PNG") else "image/jpeg"


def parse_json(content: str) -> dict:
    """The JSON object in a model answer (also inside a ``` fence or surrounded by text)."""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise AiError("model did not answer with JSON")
    try:
        value = json.loads(content[start : end + 1])
    except json.JSONDecodeError as e:
        raise AiError(f"model answered with invalid JSON: {e.msg}") from e
    if not isinstance(value, dict):
        raise AiError("model did not answer with a JSON object")
    return value


def chat(
    s: AiSettings,
    system: str,
    user: str,
    images: list[bytes] | None = None,
    json_schema: dict | None = None,
    temperature: float | None = None,
) -> tuple[str, str]:
    """Returns (text, model). Reasoning blocks some local models emit are removed.

    `images` (PNG/JPEG bytes) go along as image parts, for models that can see. With `json_schema`
    the model is asked for structured output; servers that reject `response_format` are asked
    again without it (the prompt then carries the schema).
    """
    if not s.base_url or not s.model:
        raise AiError("AI model is not configured")
    content: str | list = user
    if images:
        content = [{"type": "text", "text": user}] + [
            {
                "type": "image_url",
                "image_url": {"url": f"data:{_image_type(b)};base64,{base64.b64encode(b).decode()}"},
            }
            for b in images
        ]
    body = {
        "model": s.model,
        "temperature": s.temperature if temperature is None else temperature,
        "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
    }
    if json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": json_schema},
        }
    try:
        with _client(s) as c:
            r = c.post("/chat/completions", json=body)
            # hosted reasoning models accept only their default temperature
            if r.status_code == 400 and "temperature" in r.text:
                body.pop("temperature")
                r = c.post("/chat/completions", json=body)
            if json_schema is not None and r.status_code in (400, 422, 501):
                body.pop("response_format")
                r = c.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
    except httpx.TimeoutException as e:
        raise AiError(f"model did not answer within {s.timeout_s} s") from e
    except (httpx.HTTPError, ValueError) as e:
        raise AiError(f"{type(e).__name__}: {e}") from e
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise AiError("unexpected response from model") from e
    content = _THINK.sub("", content).strip()
    if not content:
        raise AiError("model returned an empty answer")
    return content, data.get("model") or s.model


# --- prompts -----------------------------------------------------------------------------

PROMPT_VERSION = 5

PROMPTS = {
    "de": """Du bist ein erfahrener, nüchterner Coach, der persönliche Gesundheitsdaten einordnet. \
Die Person sieht alle Zahlen bereits in ihrem Dashboard – eine Aufzählung der Werte hilft ihr nicht. \
Deine Aufgabe ist Einordnung: Was ist heute wirklich anders als sonst, wie hängt es zusammen und was folgt daraus?

So arbeitest du mit den Fakten (JSON):
- `notable_vs_usual` enthält nur Werte außerhalb des persönlichen üblichen Bereichs (Perzentile der letzten 28 Tage). \
Konzentriere dich darauf. Alles in `within_usual_range` fasst du in höchstens einem Halbsatz zusammen.
- Suche Zusammenhänge über Bereiche hinweg: Training (heute, gestern, Last-Verhältnis) ↔ Schlaf/HRV/Ruhepuls; \
Stress im Tagesverlauf ↔ Body Energy ↔ Schlaf; Energiebilanz und Protein ↔ Training und Gewichtstrend; \
Stimmung, Symptome und Journal ↔ Messwerte. Nutze `yesterday`, `day_structure`, `trends_7d_vs_previous_3w` \
und `patterns_last_60d` (persönliche Muster mit n).
- Formuliere Zusammenhänge als begründete Hypothesen („passt dazu, dass …“, „möglicherweise …“) und nenne \
höchstens die ein, zwei Zahlen, die die Aussage belegen. Muster sind beschreibend, nicht kausal; bei kleinem n sag das.
- Fehlende Daten sind nicht 0. Erwähne Lücken nur, wenn sie eine Aussage einschränken.
- `goals` sind die Ziele der Person (Nährwerte aus Waistline, Trinkziel per Formel) mit Status; Gewohnheiten mit \
`time` sind Zeitfenster (Zubettgehen, Aufstehen, letztes Koffein). Beziehe sie ein, wo sie zur Einordnung passen.
- `lab_results` sind Laborwerte als Daten: neue Werte, Werte außerhalb der Laborreferenz oder des persönlichen \
Zielbereichs, Vorwert und seit wann verwandte Supplements genommen werden. Erwähne sie nur, wenn sie neu sind oder \
zu einer Beobachtung passen (z. B. Supplement-Einnahme ↔ Verlauf eines Werts). Keine Diagnose aus Laborwerten; \
auffällige Werte ärztlich besprechen lassen.
- `strength_training`: Krafttrainings mit Übungen, Sätzen und deiner Bewertung (easy = zu leicht, hard = zu schwer), \
harte Sätze pro Muskelgruppe dieser Woche und das nächste Training laut Plan mit Vorschlag. Beziehe es auf Erholung \
(Schlaf, HRV, Ruhepuls) und Ernährung (Protein); stelle keinen eigenen Trainingsplan auf.
- Konkrete Vorschläge (z. B. zu Ernährung, Schlafzeiten, Training, Supplements) sind erwünscht; kennzeichne sie als \
Vorschläge und weise bei gesundheitlich relevanten Punkten kurz darauf hin, sie ärztlich abzuklären. Keine Diagnosen, \
keine Änderungen an Medikamenten; bei wirklich auffälligen Werten (z. B. deutlich erhöhter Ruhepuls zusammen mit \
Hauttemperatur) zur Beobachtung bzw. Abklärung raten.
- Kein Gesamt-Score, keine Floskeln, kein Lob-Überschwang.

Format (Markdown, 150–230 Wörter):
**Kurz gesagt:** ein Satz mit der wichtigsten Einordnung.
**Was auffällt:** 2–3 Punkte, jeweils Beobachtung + Bedeutung.
**Mögliche Zusammenhänge:** 1–3 Punkte, jeweils als Hypothese mit Beleg.
**Für morgen:** 1–2 konkrete, kleine Schritte, die direkt aus den Beobachtungen folgen.""",
    "en": """You are an experienced, level-headed coach interpreting personal health data. \
The person already sees every number on their dashboard – listing values does not help. \
Your job is interpretation: what is really different today, how does it connect, and what follows?

Working with the facts (JSON):
- `notable_vs_usual` only contains values outside the personal usual range (percentiles of the last 28 days). \
Focus on those. Summarize everything in `within_usual_range` in half a sentence at most.
- Look for links across domains: training (today, yesterday, load ratio) ↔ sleep/HRV/resting HR; stress over \
the day ↔ body energy ↔ sleep; energy balance and protein ↔ training and weight trend; mood, symptoms and \
journal ↔ measurements. Use `yesterday`, `day_structure`, `trends_7d_vs_previous_3w` and `patterns_last_60d`.
- Phrase links as reasoned hypotheses and quote at most the one or two numbers that support them. Patterns \
are descriptive, not causal; mention small n.
- Missing data is not zero. Mention gaps only where they limit a statement.
- `goals` are the person's goals (nutrients from Waistline, drink goal from a formula) with their status; habits \
with `time` are time windows (bedtime, wake time, last caffeine). Use them where they help the interpretation.
- `lab_results` are lab values as data: new values, values outside the lab reference or the personal target, the \
previous value and since when related supplements are taken. Mention them only when new or when they fit an \
observation (e.g. supplement intake ↔ a value's course). No diagnosis from lab values; suggest discussing unusual \
values with a doctor.
- `strength_training`: strength sessions with exercises, sets and the person's rating (easy = too easy, hard = too \
hard), hard sets per muscle group this week and the next planned workout with its suggestion. Relate it to recovery \
(sleep, HRV, resting HR) and nutrition (protein); do not write a training plan of your own.
- Concrete suggestions (nutrition, sleep times, training, supplements) are welcome; label them as suggestions and \
briefly recommend checking health-relevant points with a doctor. No diagnoses, no medication changes; for clearly \
unusual values suggest observation or a check-up.
- No overall score, no filler, no over-the-top praise.

Format (Markdown, 150–230 words):
**In short:** one sentence with the key takeaway.
**What stands out:** 2–3 points, each observation + meaning.
**Possible connections:** 1–3 points, each a hypothesis with evidence.
**For tomorrow:** 1–2 concrete small steps that follow from the observations.""",
}

WEEK_TASK = {
    "de": "Ordne diese Woche ein. Nutze `days` für Zusammenhänge zwischen einzelnen Tagen (z. B. Training → "
    "folgende Nacht) und `notable_week_vs_previous_4_weeks` für Veränderungen. Ersetze 'Für morgen' durch "
    "**Für nächste Woche**.",
    "en": "Interpret this week. Use `days` for links between single days (e.g. training → following night) and "
    "`notable_week_vs_previous_4_weeks` for changes. Replace 'For tomorrow' with **For next week**.",
}
DAY_TASK = {"de": "Ordne diesen Tag ein.", "en": "Interpret this day."}
FOCUS = {
    "de": "Persönlicher Fokus der Person (gewichte Beobachtungen danach): {focus}",
    "en": "The person's personal focus (weight observations accordingly): {focus}",
}


def summarize(conn: Connection, scope: str, day: date, lang: str) -> dict:
    s = load_settings(conn)
    if not available(s, "summary"):
        raise AiError(unavailable_reason(s, "summary"))
    shared = {"journal": s.shares("journal"), "labs": s.shares("labs")}
    facts = (
        ai_facts.day_facts(conn, day, shared) if scope == "day" else ai_facts.week_facts(conn, day, shared)
    )
    facts["prompt_version"] = PROMPT_VERSION
    task = DAY_TASK[lang] if scope == "day" else WEEK_TASK[lang]
    system = PROMPTS[lang] + (f"\n\n{FOCUS[lang].format(focus=s.focus.strip())}" if s.focus.strip() else "")
    started = time.monotonic()
    content, model = chat(
        s, system, f"{task}\n\nFakten (JSON):\n{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}"
    )
    duration = int((time.monotonic() - started) * 1000)
    row = conn.execute(
        text("""INSERT INTO ai_summary (scope, day, lang, model, context, text, duration_ms)
                VALUES (:s, :d, :l, :m, CAST(:c AS jsonb), :t, :ms)
                RETURNING id, scope, day, lang, model, context, text, duration_ms, created_at"""),
        {
            "s": scope,
            "d": day,
            "l": lang,
            "m": model,
            "c": json.dumps(facts, default=str),
            "t": content,
            "ms": duration,
        },
    ).one()
    return dict(row._mapping)


def latest(conn: Connection, scope: str, day: date, lang: str) -> dict | None:
    row = conn.execute(
        text("""SELECT id, scope, day, lang, model, context, text, duration_ms, created_at FROM ai_summary
                WHERE scope = :s AND day = :d AND lang = :l ORDER BY created_at DESC LIMIT 1"""),
        {"s": scope, "d": day, "l": lang},
    ).first()
    return dict(row._mapping) if row else None
