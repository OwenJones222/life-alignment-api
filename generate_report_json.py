# generate_report_json.py
# Full replacement — robust score extraction so raw totals/charts populate no matter how the form posts.

import io
import json
import re
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
)

# -----------------------------
# Settings
# -----------------------------
DRAW_SPIDER = False           # we’re keeping this off for now
DEBUG_LOG   = True            # prints in Render logs to help trace payloads

# -----------------------------
# Canonical structure & labels (match the current questionnaire)
# -----------------------------
PILLARS = ["health", "wealth", "self", "social"]

SUBTHEMES = {
    "health": ["Sleep & Recovery", "Physical Energy", "Mental Wellbeing", "Preventative Care"],
    "wealth": ["Income Stability", "Retirement & Pensions", "Lifestyle & Security", "Habits & Knowledge"],
    "self":   ["Fulfilment", "Growth & Learning", "Identity & Confidence", "Legacy & Purpose"],
    "social": ["Family & Relationships", "Professional Networks", "Community & Belonging", "Contribution & Impact"],
}

PILLAR_TITLES = {
    "health": "Health Pillar",
    "wealth": "Wealth Pillar",
    "self":   "Self Pillar",
    "social": "Social Pillar",
}

PILLAR_COLORS = {
    "health": "#28a745",
    "wealth": "#f1c232",
    "self":   "#4ea1d3",
    "social": "#9b59b6",
}

RANK_WEIGHT = {1: 4, 2: 3, 3: 2, 4: 1}

# -----------------------------
# Helpers
# -----------------------------
def dlog(msg):
    if DEBUG_LOG:
        print(msg)

def _as_number(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default

def _as_int_list(x):
    out = []
    for v in (x or []):
        try:
            out.append(int(float(v)))
        except Exception:
            out.append(0)
    return out

_slug_re = re.compile(r"[^a-z0-9]+")
def slug(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("&", "and")
    return _slug_re.sub("-", s).strip("-")

def mean_safe(vals):
    vals = [ _as_number(v) for v in vals if v is not None ]
    return sum(vals)/len(vals) if vals else 0.0

def pick_first(*candidates):
    for c in candidates:
        if c is not None:
            return c
    return None

# -----------------------------
# Robust extraction of sub-theme scores (0–5)
# -----------------------------
def extract_subtheme_scores(data: dict, pillar: str, labels: list[str]) -> list[float]:
    """
    Return a list of 4 numbers (0–5) for the pillar’s sub-themes.
    Priority:
      1) data['ratings'][pillar]           -> [4 floats/ints]
      2) data['scores'][pillar]            -> [4] or {label:score}
      3) data['ratings_map'][pillar]       -> {label:score}
      4) data in 'answers'/'responses'     -> derive per-label mean from nested answers
    """
    # 1) ratings[pillar] is already an array
    ratings = None
    if isinstance(data.get("ratings"), dict):
        val = data["ratings"].get(pillar)
        if isinstance(val, (list, tuple)) and len(val) == 4:
            ratings = [ _as_number(v, 0.0) for v in val ]
            dlog(f"[scores] using ratings[{pillar}] -> {ratings}")

    # 2) scores[pillar] might be an array or a dict mapping label->score
    if ratings is None and isinstance(data.get("scores"), dict):
        val = data["scores"].get(pillar)
        if isinstance(val, (list, tuple)) and len(val) == 4:
            ratings = [ _as_number(v, 0.0) for v in val ]
            dlog(f"[scores] using scores[{pillar}] -> {ratings}")
        elif isinstance(val, dict):
            # map labels in our order
            ratings = [ _as_number(val.get(lbl), 0.0) for lbl in labels ]
            if any(ratings):
                dlog(f"[scores] using scores[{pillar}] dict -> {ratings}")

    # 3) ratings_map[pillar] as dict mapping label->score
    if ratings is None and isinstance(data.get("ratings_map"), dict):
        m = data["ratings_map"].get(pillar)
        if isinstance(m, dict):
            ratings = [ _as_number(m.get(lbl), 0.0) for lbl in labels ]
            if any(ratings):
                dlog(f"[scores] using ratings_map[{pillar}] -> {ratings}")

    # 4) derive from nested answers/responses
    if ratings is None:
        # Many forms nest per-pillar answers under keys like 'answers' or 'responses'
        answers = pick_first(data.get("answers"), data.get("responses"))
        derived = [0.0]*4
        if isinstance(answers, dict):
            # Try a few likely shapes:
            # a) answers[pillar] -> dict of subtheme -> list of 5 answers
            # b) answers -> flat dict whose keys mention the pillar AND subtheme
            pillar_block = answers.get(pillar)
            label_slugs  = [slug(lbl) for lbl in labels]

            if isinstance(pillar_block, dict):
                # straight mapping label/slug -> list of numbers (0–5)
                for i, lbl in enumerate(labels):
                    s = slug(lbl)
                    # accept exact, slug, or any key containing the slug
                    cand = pick_first(
                        pillar_block.get(lbl),
                        pillar_block.get(s),
                        next((v for k, v in pillar_block.items() if s in slug(k)), None)
                    )
                    if isinstance(cand, (list, tuple)):
                        derived[i] = mean_safe(cand)
                dlog(f"[scores] derived from answers[{pillar}] block -> {derived}")
            else:
                # scan entire answers dict for entries that match this pillar + subtheme
                for i, lbl in enumerate(labels):
                    s = slug(lbl)
                    matches = []
                    for k, v in answers.items():
                        ks = slug(str(k))
                        if pillar in ks and s in ks and isinstance(v, (list, tuple)):
                            matches.extend([_as_number(x, 0.0) for x in v])
                    if matches:
                        derived[i] = mean_safe(matches)

                dlog(f"[scores] derived by scanning answers for pillar {pillar} -> {derived}")

        ratings = derived

    # Ensure valid list
    if not isinstance(ratings, list) or len(ratings) != 4:
        ratings = [0.0, 0.0, 0.0, 0.0]

    return ratings

def extract_ranks(data: dict, pillar: str) -> list[int]:
    """
    Ranks per sub-theme (1..4), array of 4. Tolerant to missing/partial.
    """
    rk = None
    # Newer schema
    if isinstance(data.get("ranks_per_subtheme"), dict):
        val = data["ranks_per_subtheme"].get(pillar)
        if isinstance(val, (list, tuple)) and len(val) == 4:
            rk = _as_int_list(val)

    # Legacy pillar-level ranks (not used anymore but tolerate)
    if rk is None and isinstance(data.get("ranks"), dict):
        val = data["ranks"].get(pillar)
        if isinstance(val, (list, tuple)) and len(val) == 4:
            rk = _as_int_list(val)

    if rk is None:
        rk = [1, 2, 3, 4]  # sensible default

    return rk

def normalize_inputs(data):
    """
    Build ratings (0–5 per subtheme) and ranks (1..4 per subtheme) for each pillar
    using any shape we can find in the payload.
    """
    ratings, ranks = {}, {}

    for pillar in PILLARS:
        labels = SUBTHEMES[pillar]

        # Scores 0–5 for each subtheme, robustly extracted
        r = extract_subtheme_scores(data, pillar, labels)

        # Per-subtheme ranks 1..4 (each used once per pillar)
        rk = extract_ranks(data, pillar)

        # Basic sanity checks
        if len(r) != 4:
            r = [0.0, 0.0, 0.0, 0.0]
        if len(rk) != 4:
            rk = [1, 2, 3, 4]

        ratings[pillar] = r
        ranks[pillar]   = rk

        dlog(f"[parsed] {pillar}: ratings={r} ranks={rk}")

    return ratings, ranks

# -----------------------------
# Calculations
# -----------------------------
def rating_to_strength(score):          # 0–5 → 0–25
    return float(score) * 5.0

def compute_priority_gap(score, rank):  # larger gap = lower score + higher priority
    return (5.0 - float(score)) * RANK_WEIGHT.get(rank, 1)

def pillar_summary_rows(ratings, ranks):
    rows = []
    for pillar in PILLARS:
        r = ratings[pillar]; rk = ranks[pillar]
        raw_total = sum(rating_to_strength(x) for x in r)
        weighted_total = sum(rating_to_strength(s) * RANK_WEIGHT.get(rr, 1)/4 for s, rr in zip(r, rk))
        scaled = weighted_total / 2.0  # for 0–50 presentation
        rows.append((pillar.capitalize(), f"{raw_total:.1f}", f"{weighted_total:.1f}", f"{scaled:.1f}", ", ".join(str(x) for x in rk)))
    return rows

def find_top_gaps(ratings, ranks):
    per_pillar, overall = [], ("", -1, -1)
    for pillar in PILLARS:
        r, rk = ratings[pillar], ranks[pillar]
        gaps = [compute_priority_gap(s, rr) for s, rr in zip(r, rk)]
        idx = int(np.argmax(gaps))
        per_pillar.append((pillar, idx, gaps[idx]))
        if gaps[idx] > overall[2]:
            overall = (pillar, idx, gaps[idx])
    return per_pillar, overall

# -----------------------------
# Plotting
# -----------------------------
def _bar_plot_for_pillar(pillar, ratings, ranks):
    x_labels = SUBTHEMES[pillar]
    scores   = ratings[pillar]
    rk       = ranks[pillar]

    strengths = [rating_to_strength(s) for s in scores]
    gaps      = [compute_priority_gap(s, rr) for s, rr in zip(scores, rk)]

    x = np.arange(4)
    width = 0.35
    plt.close("all")
    fig, ax = plt.subplots(figsize=(10, 6))
    c = PILLAR_COLORS[pillar]

    ax.bar(x - width/2, strengths, width, label="Strength (0–25)", color=c, alpha=0.9)
    ax.bar(x + width/2, gaps, width, label="Priority Gap (0–25)", color=c, alpha=0.55)

    for xi, rnk in zip(x, rk):
        ax.text(xi, max(strengths[xi], gaps[xi]) + 0.6, f"rank {int(rnk)}", ha="center", va="bottom", fontsize=9, color="#444")

    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, rotation=-12, ha="right", fontsize=9)
    ax.set_ylim(0, 25)
    ax.set_ylabel("0–25 scale\n(higher Strength is better; higher Gap needs attention)")
    ax.set_title(f"{pillar.capitalize()} – Strength vs Priority Gap (rank 1 = most important)")
    ax.legend(loc="upper right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()

# -----------------------------
# PDF Assembly
# -----------------------------
def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle(name="H1Big", fontSize=20, leading=24, spaceAfter=12))
    s.add(ParagraphStyle(name="Small", fontSize=9, leading=12))
    return s

def _cover(data):
    who = data.get("name") or "Your Life Alignment"
    when = datetime.utcnow().strftime("%d %b %Y")
    return [Paragraph("Life Alignment Diagnostic", _styles()["H1Big"]),
            Paragraph(f"{who}", _styles()["Small"]),
            Paragraph(f"Generated: {when} (UTC)", _styles()["Small"]),
            Spacer(1, 12)]

def _priority_focus(ratings, ranks):
    story = [Paragraph("Priority Focus Summary", _styles()["H1Big"])]
    per_pillar, overall = find_top_gaps(ratings, ranks)
    for pillar, idx, gap in per_pillar:
        s = ratings[pillar][idx]; r = ranks[pillar][idx]
        label = SUBTHEMES[pillar][idx]
        story.append(Paragraph(f"<b>{pillar.capitalize()} → {label}</b> (Gap {gap:.1f}; Strength {rating_to_strength(s):.0f}/25; rank {r})", _styles()["Small"]))
        story.append(Spacer(1, 6))
    if overall[0]:
        op, oi, og = overall
        s = ratings[op][oi]; r = ranks[op][oi]
        story.append(Paragraph(f"<b>Overall largest gap:</b> {op.capitalize()} → {SUBTHEMES[op][oi]} (Gap {og:.1f}; Strength {rating_to_strength(s):.0f}/25; rank {r})", _styles()["Small"]))
    return story

def _pillar_page(pillar, ratings, ranks):
    story = [Paragraph(PILLAR_TITLES[pillar], _styles()["H1Big"])]
    im = Image(io.BytesIO(_bar_plot_for_pillar(pillar, ratings, ranks)))
    im._restrictSize(500, 320)
    story.append(im)
    story.append(Spacer(1, 6))
    line = ", ".join(f"{label}: {rk}" for label, rk in zip(SUBTHEMES[pillar], ranks[pillar]))
    story.append(Paragraph(f"<i>Participant importance ranks (1 = most important)</i>: {line}", _styles()["Small"]))
    return story

def _summary_table(ratings, ranks):
    data = [["Pillar", "Raw Total (0–100)", "Weighted Total (0–100)", "Weighted Scaled (0–50)", "Ranks used (1=most)"]] + pillar_summary_rows(ratings, ranks)
    t = Table(data, colWidths=[90,120,140,140,150])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.lightgrey),("GRID",(0,0),(-1,-1),0.25,colors.grey),("FONTSIZE",(0,0),(-1,-1),9)]))
    return t

def build_pdf_report(payload, out_pdf=None):
    if isinstance(payload, (str, bytes)):
        try: data = json.loads(payload)
        except Exception:
            data = {}
    else:
        data = payload or {}

    ratings, ranks = normalize_inputs(data)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=36, bottomMargin=36, leftMargin=42, rightMargin=42)
    story = []
    story += _cover(data)
    story += _priority_focus(ratings, ranks)
    story.append(PageBreak())
    for pillar in PILLARS:
        story += _pillar_page(pillar, ratings, ranks)
        story.append(PageBreak())
    story.append(Paragraph("Summary", _styles()["H1Big"]))
    story.append(_summary_table(ratings, ranks))
    doc.build(story)
    pdf = buf.getvalue()

    if out_pdf:
        with open(out_pdf, "wb") as f: f.write(pdf)
        return out_pdf
    return pdf
