# generate_report_json.py
# Full replacement – tolerant input parsing, pillar charts with ranks above bars,
# real sub-theme labels, no weight text, spiderweb disabled.

import io
import json
from datetime import datetime

# Matplotlib / plotting
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ReportLab / PDF
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    PageBreak,
    Table,
    TableStyle,
)

# -----------------------------
# Switches / diagnostics
# -----------------------------
DRAW_SPIDER = False   # temporarily disabled
DEBUG_LOG   = True    # prints short parsing logs to Render logs

# -----------------------------
# Canonical structure & labels
# -----------------------------
PILLARS = ["health", "wealth", "self", "social"]

SUBTHEMES = {
    "health": ["Physical Energy", "Mental Health", "Workload / Balance", "Preventative Care"],
    "wealth": ["Income Stability", "Retirement & Pensions", "Lifestyle & Security", "Habits & Knowledge"],
    "self":   ["Purpose & Direction", "Growth & Learning", "Mindset & Resilience", "Joy & Fulfilment"],
    "social": ["Family & Relationships", "Professional Networks", "Community & Belonging", "Contribution & Impact"],
}

PILLAR_TITLES = {
    "health": "Health Pillar",
    "wealth": "Wealth Pillar",
    "self":   "Self Pillar",
    "social": "Social Pillar",
}

PILLAR_COLORS = {
    "health": "#28a745",  # green-ish
    "wealth": "#f1c232",  # amber
    "self":   "#4ea1d3",  # blue
    "social": "#9b59b6",  # purple
}

# rank -> weight
RANK_WEIGHT = {1: 4, 2: 3, 3: 2, 4: 1}

# -------------- Input parsing (tolerant) --------------

def _as_int_list(x):
    out = []
    for v in (x or []):
        if v in ("", None):
            out.append(0)
        else:
            try:
                out.append(int(v))
            except Exception:
                try:
                    out.append(int(round(float(v))))
                except Exception:
                    out.append(0)
    return out

def _try_new_shape(data, pillar):
    """
    New shape from the updated form:
      data["ratings"][pillar] -> [0..5] * 4
      data["ranks_per_subtheme"][pillar] -> [1..4] * 4
    """
    ratings = None
    ranks   = None
    if isinstance(data.get("ratings"), dict):
        ratings = _as_int_list(data["ratings"].get(pillar))
        if len(ratings) != 4:
            ratings = None
    if isinstance(data.get("ranks_per_subtheme"), dict):
        ranks = _as_int_list(data["ranks_per_subtheme"].get(pillar))
        if len(ranks) != 4:
            ranks = None
    return ratings, ranks

def _try_legacy_shape(data, pillar):
    """
    Older versions we used early on:
      data["scores"][pillar][subkey] -> 0..5 (dict) *or* list of 4
      data["ranks"][pillar]          -> single pillar rank (1..4).
    If only a single pillar rank exists, we default per-subtheme ranks to [2,2,2,2].
    """
    ratings = None
    ranks   = None

    scores = data.get("scores", {}).get(pillar, {})
    if isinstance(scores, dict) and scores:
        vals = []
        for sublabel in SUBTHEMES[pillar]:
            candidates = [
                sublabel,
                sublabel.lower().replace(" ", "_"),
                sublabel.lower(),
            ]
            v = None
            for k in candidates:
                if k in scores:
                    v = scores[k]
                    break
            vals.append(0 if v in (None, "") else int(float(v)))
        if len(vals) == 4:
            ratings = vals
    elif isinstance(scores, list) and len(scores) == 4:
        ratings = _as_int_list(scores)

    pr = data.get("ranks", {}).get(pillar)
    if pr is not None:
        try:
            pr = int(pr)
            if pr in (1, 2, 3, 4):
                ranks = [2, 2, 2, 2]  # neutral default if only pillar rank was given
        except Exception:
            pass

    return ratings, ranks

def _fallback_guess(data, pillar):
    """
    Very loose fallback: health_1..health_4, health_sub1..sub4
    """
    ratings = []
    loose_keys = [
        f"{pillar}_1", f"{pillar}_2", f"{pillar}_3", f"{pillar}_4",
        f"{pillar}_sub1", f"{pillar}_sub2", f"{pillar}_sub3", f"{pillar}_sub4",
    ]
    for k in loose_keys[:4]:
        v = data.get(k)
        ratings.append(0 if v in (None, "") else int(float(v)))
    if len(ratings) == 4:
        return ratings, None
    return None, None

def normalize_inputs(data):
    """
    Returns normalized dicts:
      ratings[pillar] -> [int 0..5]*4
      ranks[pillar]   -> [int 1..4]*4 (if missing/invalid, defaults to [1,2,3,4])
    """
    ratings = {}
    ranks   = {}

    if DEBUG_LOG:
        try:
            print(f"[payload] top-level keys: {list(data.keys())}")
            if isinstance(data.get("ratings"), dict):
                print(f"[payload] ratings keys: {list(data['ratings'].keys())}")
            if isinstance(data.get("ranks_per_subtheme"), dict):
                print(f"[payload] ranks_per_subtheme keys: {list(data['ranks_per_subtheme'].keys())}")
        except Exception:
            pass

    for pillar in PILLARS:
        r_new, rk_new = _try_new_shape(data, pillar)
        r_old, rk_old = _try_legacy_shape(data, pillar)
        r_fb, rk_fb   = _fallback_guess(data, pillar)

        r = r_new or r_old or r_fb
        if not r or len(r) != 4:
            r = [0, 0, 0, 0]

        rk = rk_new or rk_old or rk_fb
        if not rk or len(rk) != 4:
            rk = [1, 2, 3, 4]

        ratings[pillar] = r
        ranks[pillar]   = rk

        if DEBUG_LOG:
            print(f"[parsed] {pillar}: ratings={ratings[pillar]} ranks={ranks[pillar]}")

    return ratings, ranks


# -------------- Computation helpers --------------

def rating_to_strength(score_0_to_5):
    """Plot strength as 0..25 (visual space to match previous charts)."""
    return float(score_0_to_5) * 5.0

def compute_priority_gap(score, rank):
    """
    Simple interpretable 'gap' scaled similarly (0..25-ish):
      bigger when rank is high (1 is most important -> weight 4)
      and the score is low.
    """
    weight = RANK_WEIGHT.get(int(rank), 1)
    gap = (5.0 - float(score)) * weight
    return gap  # this typically ranges 0..20

def pillar_summary_rows(ratings, ranks):
    """
    Return list of (pillar, raw_total, weighted_total, scaled_0_50, ranks_used_text)
    """
    rows = []
    for pillar in PILLARS:
        r = ratings[pillar]
        rk = ranks[pillar]
        raw_total = sum([rating_to_strength(x) for x in r])  # 0..100
        weighted_total = 0.0
        for s, rr in zip(r, rk):
            w = RANK_WEIGHT.get(int(rr), 1)
            weighted_total += rating_to_strength(s) * w / 4.0  # keep in 0..100-ish
        scaled = weighted_total / 2.0  # to 0..50 scale (optional)
        rows.append((
            pillar.capitalize(),
            f"{raw_total:.1f}",
            f"{weighted_total:.1f}",
            f"{scaled:.1f}",
            ", ".join(str(x) for x in rk)
        ))
    return rows

def find_top_gaps(ratings, ranks):
    """
    For each pillar, find the subtheme with the largest gap.
    Also return the overall largest gap (pillar, index, gap).
    """
    per_pillar = []
    overall = ("", -1, -1.0)  # (pillar, idx, gap)
    for pillar in PILLARS:
        r = ratings[pillar]
        rk = ranks[pillar]
        gaps = [compute_priority_gap(s, rr) for s, rr in zip(r, rk)]
        best_idx = int(np.argmax(gaps))
        per_pillar.append((pillar, best_idx, gaps[best_idx]))
        if gaps[best_idx] > overall[2]:
            overall = (pillar, best_idx, gaps[best_idx])
    return per_pillar, overall


# -------------- Plotting --------------

def _bar_plot_for_pillar(pillar, ratings, ranks, fig_w=10, fig_h=6):
    """
    Return PNG bytes of the pillar bar chart.
    Shows Strength vs Priority Gap, subtheme labels on x,
    and the rank number above each bar pair (small text).
    """
    x_labels = SUBTHEMES[pillar]
    scores   = ratings[pillar]
    rk       = ranks[pillar]

    strengths = [rating_to_strength(s) for s in scores]
    gaps      = [compute_priority_gap(s, rr) for s, rr in zip(scores, rk)]

    x = np.arange(4)
    width = 0.35

    plt.close("all")
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    # bars
    c_main = PILLAR_COLORS[pillar]
    c_gap  = colors.HexColor(c_main)
    strength_bars = ax.bar(x - width/2, strengths, width, label="Strength (0–25)", color=c_main, alpha=0.9)
    gap_bars      = ax.bar(x + width/2, gaps,      width, label="Priority Gap (0–25)", color=c_main, alpha=0.55)

    # gridlines to help read values (same as older look)
    for y in [12, 18]:
        ax.axhline(y, color="#bbbbbb", linestyle="--", linewidth=1, alpha=0.6)

    # rank labels above the bar pairs
    for xi, rnk in zip(x, rk):
        ax.text(xi, max(strengths[xi], gaps[xi]) + 0.6, f"rank {int(rnk)}",
                ha="center", va="bottom", fontsize=9, color="#444444")

    # x tick labels (subthemes)
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, rotation= -12, ha="right", fontsize=9)

    ax.set_ylim(0, max(25, max(strengths + gaps) + 3))
    ax.set_ylabel("0–25 scale\n(higher Strength is better; higher Gap needs attention)")
    ax.set_title(f"{pillar.capitalize()} – Strength vs Priority Gap (rank 1 = most important)", pad=10)

    ax.legend(loc="upper right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()


# -------------- PDF assembly --------------

def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1Big", fontSize=20, leading=24, spaceAfter=12, textColor=colors.black))
    styles.add(ParagraphStyle(name="H2", fontSize=14, leading=18, spaceAfter=6, textColor=colors.black))
    styles.add(ParagraphStyle(name="Small", fontSize=9, leading=12, textColor=colors.black))
    return styles

def _cover_block(data):
    who  = data.get("name") or data.get("participant") or "Your Life Alignment"
    when = datetime.utcnow().strftime("%d %b %Y")
    return [
        Paragraph("Life Alignment Diagnostic", _styles()["H1Big"]),
        Paragraph(f"{who}", _styles()["H2"]),
        Spacer(1, 6),
        Paragraph(f"Generated: {when} (UTC)", _styles()["Small"]),
        Spacer(1, 18),
    ]

def _priority_focus_block(ratings, ranks):
    story = []
    story.append(Paragraph("Priority Focus Summary", _styles()["H1Big"]))

    per_pillar, overall = find_top_gaps(ratings, ranks)
    for pillar, idx, gap in per_pillar:
        s = ratings[pillar][idx]
        r = ranks[pillar][idx]
        label = SUBTHEMES[pillar][idx]
        text = (
            f"<b>{pillar.capitalize()} → {label}</b> "
            f"(Gap {gap:.1f}; Strength {rating_to_strength(s):.0f}/25; rank {int(r)})<br/>"
            f"This represents the area within your <b>{pillar.capitalize()}</b> Pillar in which your individual answers, "
            f"and your overall priority given to <b>{label}</b>, are out of alignment. "
            f"It is therefore the area to concentrate on first in order to make the biggest improvement for you."
        )
        story.append(Paragraph(text, _styles()["Small"]))
        story.append(Spacer(1, 6))

    op, oi, og = overall
    if op:
        s = ratings[op][oi]
        r = ranks[op][oi]
        label = SUBTHEMES[op][oi]
        story.append(Paragraph("<b>Overall largest gap</b>", _styles()["H2"]))
        text = (
            f"<b>{op.capitalize()} → {label}</b> "
            f"(Gap {og:.1f}; Strength {rating_to_strength(s):.0f}/25; rank {int(r)})<br/>"
            f"This represents the single biggest opportunity to focus on next."
        )
        story.append(Paragraph(text, _styles()["Small"]))
        story.append(Spacer(1, 6))

    return story

def _pillar_page(pillar, ratings, ranks):
    story = []
    story.append(Paragraph(PILLAR_TITLES[pillar], _styles()["H1Big"]))

    # chart image
    img_bytes = _bar_plot_for_pillar(pillar, ratings, ranks)
    im = Image(io.BytesIO(img_bytes))
    im._restrictSize(500, 320)
    story.append(im)
    story.append(Spacer(1, 6))

    # line with ranks summary
    rk = ", ".join(str(int(x)) for x in ranks[pillar])
    line = (
        f"<i>Participant importance ranks (1 = most important)</i>: "
        f"{SUBTHEMES[pillar][0]}: {ranks[pillar][0]}, "
        f"{SUBTHEMES[pillar][1]}: {ranks[pillar][1]}, "
        f"{SUBTHEMES[pillar][2]}: {ranks[pillar][2]}, "
        f"{SUBTHEMES[pillar][3]}: {ranks[pillar][3]}"
    )
    story.append(Paragraph(line, _styles()["Small"]))
    story.append(Spacer(1, 6))

    return story

def _summary_table(ratings, ranks):
    headings = ["Pillar", "Raw Total (0–100)", "Weighted Total (0–100)", "Weighted Scaled (0–50)", "Ranks used (1=most)"]
    rows = pillar_summary_rows(ratings, ranks)
    data = [headings] + rows
    t = Table(data, colWidths=[90, 120, 140, 140, 160])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("TEXTCOLOR", (0,0), (-1,0), colors.black),
        ("ALIGN", (1,1), (-1,-1), "CENTER"),
        ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("BOTTOMPADDING", (0,0), (-1,0), 6),
    ]))
    return t

# -------------- Public entry point --------------

def build_pdf_report(payload, out_pdf=None):
    """
    Public entry point used by the API.
    Accepts either:
      build_pdf_report(data_dict)
      build_pdf_report(data_dict, out_pdf_path)

    Returns PDF bytes if out_pdf is None; otherwise writes the file and returns out path.
    """
    # If payload arrives as JSON string, decode
    if isinstance(payload, (str, bytes)):
        try:
            data = json.loads(payload)
        except Exception:
            data = {}
    else:
        data = payload or {}

    ratings, ranks = normalize_inputs(data)

    # Assemble report
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=36, bottomMargin=36, leftMargin=42, rightMargin=42)
    story = []

    # Cover
    story.extend(_cover_block(data))
    story.append(Spacer(1, 6))

    # Priority summary (first)
    story.extend(_priority_focus_block(ratings, ranks))
    story.append(PageBreak())

    # Pillar pages
    for pillar in PILLARS:
        story.extend(_pillar_page(pillar, ratings, ranks))
        story.append(PageBreak())

    # Spiderweb – disabled for now
    if DRAW_SPIDER:
        # kept intentionally blank until we reinstate with a square figure
        pass

    # Summary table
    story.append(Paragraph("Summary", _styles()["H1Big"]))
    story.append(_summary_table(ratings, ranks))

    doc.build(story)
    pdf_bytes = buffer.getvalue()

    if out_pdf:
        with open(out_pdf, "wb") as f:
            f.write(pdf_bytes)
        return out_pdf

    return pdf_bytes
