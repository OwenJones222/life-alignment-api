# generate_report_json.py
# Full replacement – aligned to latest questionnaire wording (Sleep & Recovery, etc.)

import io
import json
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
DRAW_SPIDER = False
DEBUG_LOG   = True

# -----------------------------
# Canonical structure & labels
# -----------------------------
PILLARS = ["health", "wealth", "self", "social"]

# ✅ Updated to match the latest front-end questionnaire
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
def _as_int_list(x):
    out = []
    for v in (x or []):
        try:
            out.append(int(float(v)))
        except Exception:
            out.append(0)
    return out

def normalize_inputs(data):
    """Tolerant parser for ratings and ranks."""
    ratings, ranks = {}, {}

    for pillar in PILLARS:
        r = []
        rk = []

        if isinstance(data.get("ratings"), dict):
            r = _as_int_list(data["ratings"].get(pillar))
        if isinstance(data.get("ranks_per_subtheme"), dict):
            rk = _as_int_list(data["ranks_per_subtheme"].get(pillar))

        if not r or len(r) != 4:
            r = [0, 0, 0, 0]
        if not rk or len(rk) != 4:
            rk = [1, 2, 3, 4]

        ratings[pillar] = r
        ranks[pillar]   = rk

        if DEBUG_LOG:
            print(f"[parsed] {pillar}: {r} ranks={rk}")

    return ratings, ranks

# -----------------------------
# Calculations
# -----------------------------
def rating_to_strength(score): return float(score) * 5.0
def compute_priority_gap(score, rank): return (5.0 - float(score)) * RANK_WEIGHT.get(rank, 1)

def pillar_summary_rows(ratings, ranks):
    rows = []
    for pillar in PILLARS:
        r = ratings[pillar]; rk = ranks[pillar]
        raw_total = sum(rating_to_strength(x) for x in r)
        weighted_total = sum(rating_to_strength(s) * RANK_WEIGHT.get(rr, 1)/4 for s, rr in zip(r, rk))
        scaled = weighted_total / 2.0
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
        except Exception: data = {}
    else: data = payload or {}

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
