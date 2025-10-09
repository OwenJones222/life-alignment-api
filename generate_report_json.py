# generate_report_json.py
# OPTION A: Branded layout wrapped around your existing logic
# - Keeps your calculations, ranks, gaps, wildcards, and summary exactly as-is
# - Adds: cover page (no header/footer), intro page, branded header/footer on internals
# - Updates chart colours: Strength = sage (#e2ebca), Priority Gap = teal (#1b6c7a)
# - Supports both signatures:
#       build_pdf_report(data) -> bytes
#       build_pdf_report_from_payload(data, out_pdf) -> None

import io
import os
from typing import Dict, List, Tuple

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle, KeepTogether
)
from reportlab.lib import colors
from reportlab.lib.colors import HexColor

# -----------------------
# Branding
# -----------------------
BRAND = {
    "name": "Life Alignment Diagnostic",
    "logo_path": os.getenv("BRAND_LOGO", "assets/trailkube-logo.png"),
    "sage": "#e2ebca",   # Strength bars
    "teal": "#1b6c7a",   # Priority Gap bars + headers
    "mint": "#31dea4",   # thin accent line
}

SAGE = HexColor(BRAND["sage"])
TEAL = HexColor(BRAND["teal"])
MINT = HexColor(BRAND["mint"])


# -----------------------
# Matplotlib (existing)
# -----------------------
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ---------- helpers (existing logic preserved) ----------

def _rank_to_scale(rank: int) -> float:
    """Map rank -> priority scale, keeping Gap within 0–25."""
    return {1: 1.00, 2: 0.75, 3: 0.50, 4: 0.25}.get(int(rank), 0.25)


def _pillar_slices() -> Dict[str, List[Tuple[int, int]]]:
    """
    20 items per pillar, 4 sub-themes, 5 questions each, sequential.
    Returns per-pillar list of (start, end_exclusive) slices.
    """
    five = [(0, 5), (5, 10), (10, 15), (15, 20)]
    return {
        "health": five,
        "wealth": five,
        "self": five,
        "social": five,
    }


def _sum_subtheme(values: List[int], slice_tuple: Tuple[int, int]) -> int:
    a, b = slice_tuple
    # Values are 0..5, five items per slice, so total 0..25
    return sum(int(v) for v in values[a:b])


# ------- NEW: styles & page furniture (non-disruptive) -------

def _styles():
    s = getSampleStyleSheet()
    # Base tweaks
    s["BodyText"].fontSize = 10
    s["BodyText"].leading = 14
    # Extra styles
    if "TitleXL" not in s:
        s.add(ParagraphStyle(name="TitleXL", fontSize=28, leading=32, textColor=TEAL, spaceAfter=10))
    if "H1Teal" not in s:
        s.add(ParagraphStyle(name="H1Teal", parent=s["Heading1"], textColor=TEAL))
    if "H2Teal" not in s:
        s.add(ParagraphStyle(name="H2Teal", parent=s["Heading2"], textColor=TEAL))
    if "SmallGrey" not in s:
        s.add(ParagraphStyle(name="SmallGrey", fontSize=9, leading=12, textColor=colors.grey))
    return s


def _safe_logo(max_w=140, max_h=140):
    path = BRAND["logo_path"]
    if not os.path.exists(path):
        return None
    img = Image(path)
    img._restrictSize(max_w, max_h)
    return img


def _on_page(canvas, doc):
    """
    Branded header/footer on ALL internal pages.
    We skip page 1 (cover) by checking doc.page == 1.
    """
    if doc.page == 1:
        return  # no header/footer on cover

    canvas.saveState()

    # Header band
    band_h = 16
    canvas.setFillColor(TEAL)
    canvas.rect(0, doc.height + doc.topMargin, doc.width + doc.leftMargin + doc.rightMargin, band_h, stroke=0, fill=1)
    # Mint accent line
    canvas.setFillColor(MINT)
    canvas.rect(0, doc.height + doc.topMargin - 2, doc.width + doc.leftMargin + doc.rightMargin, 2, stroke=0, fill=1)
    # Title (left)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(doc.leftMargin, doc.height + doc.topMargin + 3, BRAND["name"])

    # Footer
    canvas.setFillColor(colors.grey)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(doc.leftMargin, 1.2*cm, "TrailKube • Life Alignment")
    canvas.drawRightString(doc.leftMargin + doc.width, 1.2*cm, f"Page {doc.page}")

    canvas.restoreState()


def _section_header(title: str):
    s = _styles()
    line = Table([[""]], colWidths=[160*mm], rowHeights=[2])
    line.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), MINT)]))
    return [Paragraph(title, s["H1Teal"]), Spacer(0, 2), line, Spacer(0, 8)]


# ---------- existing function with BRAND colours ----------
def _draw_pillar_chart(
    pillar_label: str,
    subtheme_labels: List[str],
    strengths: List[float],
    gaps: List[float],
    ranks: List[int],
) -> bytes:
    """Return PNG bytes of the chart (colours updated to match brand)."""
    fig, ax = plt.subplots(figsize=(7.8, 5.0), dpi=150)  # fits A4 nicely

    x = range(len(subtheme_labels))
    # Strength = SAGE; Gap = TEAL
    bar1 = ax.bar(x, strengths, width=0.35, label="Strength (0–25)", color=SAGE)
    bar2 = ax.bar([i + 0.35 for i in x], gaps, width=0.35, label="Priority Gap (0–25)", color=TEAL)

    # rank above Strength bars
    for rect, r in zip(bar1, ranks):
        height = rect.get_height()
        ax.text(
            rect.get_x() + rect.get_width()/2,
            max(height, 0) + 0.5,
            f"rank {r}",
            ha="center", va="bottom",
            fontsize=8
        )

    ax.set_title(f"{pillar_label} – Strength vs Priority Gap (rank 1 = most important)", fontsize=10)
    ax.set_ylim(0, 25)
    ax.set_ylabel("0–25 scale\n(higher Strength is better; higher Gap needs attention)", fontsize=8)
    ax.set_xticks([i + 0.175 for i in x])
    ax.set_xticklabels(subtheme_labels, rotation=12, ha="right", fontsize=8)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.legend(fontsize=8, loc="upper right")

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def _paragraph(text: str):
    return Paragraph(text, getSampleStyleSheet()["BodyText"])


def _heading(text: str, level=1):
    styles = getSampleStyleSheet()
    style = styles["Heading1"] if level == 1 else styles["Heading2"]
    return Paragraph(text, style)


# -----------------------
# NEW: cover & intro pages
# -----------------------
def _cover_story():
    s = _styles()
    story = []
    logo = _safe_logo()
    if logo:
        story += [Spacer(0, 24), logo, Spacer(0, 18)]

    # Title + date (no client name for now)
    story += [
        Paragraph("LIFE ALIGNMENT DIAGNOSTIC", s["TitleXL"]),
        Spacer(0, 6),
        Paragraph("Generated on: ", s["SmallGrey"]),
        Paragraph(_today_str(), s["BodyText"]),
        Spacer(0, 18),
    ]

    # Bottom band echo
    band = Table([[""]], colWidths=[160*mm], rowHeights=[14])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TEAL),
        ("LINEABOVE", (0, 0), (-1, -1), 4, MINT),
    ]))
    # Push band toward bottom with a large spacer (approx position; auto-paginates if needed)
    story += [Spacer(0, 320), band, PageBreak()]
    return story


def _intro_story():
    s = _styles()
    blocks = [
        Paragraph("How to read your results", s["H2Teal"]),
        Paragraph(
            "Each pillar page shows Strength vs Priority Gap by sub-category. "
            "Below the chart you’ll find any Wildcard reflections you entered. The guidance explains "
            "why a gap is big or small (e.g., when something is ‘most important’ to you but scored low), "
            "so you can choose a small action with the biggest positive effect.",
            s["BodyText"]
        ),
        Spacer(0, 6),
        Paragraph(
            "Tips: High Strength (4s/5s on individual items) + ‘most important’ usually yields a small gap "
            "— you’re likely on track. ‘Most important’ + low scores (1s/2s) often yields a larger gap "
            "— this is your best opportunity to focus first.",
            s["BodyText"]
        ),
        PageBreak()
    ]
    return blocks


def _today_str():
    import datetime
    return datetime.datetime.now().strftime("%d %b %Y")


# ---------- main PDF builder (existing logic; wrapped with branding) ----------

def _build(doc_buf: io.BytesIO, data: dict) -> None:
    styles = getSampleStyleSheet()
    s = _styles()  # ensure styles are registered
    story = []

    meta = data.get("meta", {})
    pillars_meta = meta.get("pillars", [])
    answers = data.get("answers", {})
    ranks_by_pillar = data.get("importance", {})
    wild = data.get("wildcards", {}) or {}

    slices = _pillar_slices()

    # ---- NEW: Cover + Intro (cover has no header/footer; handled in on_page)
    story += _cover_story()
    story += _intro_story()

    # Collect focus info for summary
    per_pillar_focus = []  # (pillar_label, subtheme_label, gap, strength, rank)
    overall_best = None    # same tuple with max gap

    for pillar_info in pillars_meta:
        key = pillar_info["key"]
        pillar_label = pillar_info["label"]
        subtheme_labels = pillar_info["subthemes"]  # 4 labels in order

        # Answers -> list of 20 numbers
        pillar_answers = [int(x["value"]) for x in answers.get(key, [])]

        # Ranks -> 4 integers [1..4] aligned to subtheme order
        ranks_arr = [int(x) for x in ranks_by_pillar.get(key, [1, 2, 3, 4])]

        # Strengths per subtheme (sum of five answers)
        st = [_sum_subtheme(pillar_answers, sl) for sl in slices[key]]  # 4 numbers 0..25

        # Priority Gaps per subtheme (scaled to 0–25 using rank scale)
        gaps = [(25 - s_val) * _rank_to_scale(rank) for s_val, rank in zip(st, ranks_arr)]

        # Chart image
        img_bytes = _draw_pillar_chart(pillar_label, subtheme_labels, st, gaps, ranks_arr)
        img = Image(io.BytesIO(img_bytes))
        img._restrictSize(180*mm, 110*mm)

        # Pillar heading + chart
        story += _section_header(f"{pillar_label} Pillar")
        story.append(img)
        story.append(Spacer(0, 2*mm))

        # Rank line under chart
        rank_pairs = ", ".join([f"{lbl}: {rk}" for lbl, rk in zip(subtheme_labels, ranks_arr)])
        story.append(Paragraph(
            f"<b>Participant importance ranks (1 = most important):</b> {rank_pairs}",
            styles["BodyText"]
        ))
        story.append(Spacer(0, 2*mm))

        # Wildcards for this pillar (show any that match this key)
        # Expecting keys like wild_health_1..5 etc.
        story.append(Paragraph("<b>Wildcard reflections (not scored):</b>", styles["BodyText"]))
        any_wc = False
        for i in range(1, 5 + 1):
            wkey = f"wild_{key}_{i}"
            if wkey in wild and str(wild[wkey]).strip():
                any_wc = True
                story.append(Paragraph(f"• {wild[wkey]}", styles["BodyText"]))
        if not any_wc:
            story.append(Paragraph("—", styles["BodyText"]))
        story.append(Spacer(0, 4*mm))

        # Largest gap inside this pillar
        max_idx = max(range(4), key=lambda i: gaps[i] if gaps[i] is not None else -1)
        best_tuple = (
            pillar_label,
            subtheme_labels[max_idx],
            float(gaps[max_idx]),
            float(st[max_idx]),
            int(ranks_arr[max_idx]),
        )
        per_pillar_focus.append(best_tuple)
        if overall_best is None or best_tuple[2] > overall_best[2]:
            overall_best = best_tuple

        story.append(PageBreak())

    # Priority Focus Summary
    story += _section_header("Priority Focus Summary")
    if per_pillar_focus:
        for (pill, sub, gap, strength, rk) in per_pillar_focus:
            story.append(Paragraph(
                f"<b>{pill} → {sub}</b> "
                f"(Gap {gap:.1f}; Strength {strength:.1f}/25; rank {rk})",
                styles["BodyText"]
            ))
            story.append(Paragraph(
                f"This represents the area within your <b>{pill}</b> pillar in which your "
                f"individual answers and your stated priority for <b>{sub}</b> are out of alignment. "
                "It’s a strong candidate to focus on first for the biggest impact.",
                styles["BodyText"]
            ))
            story.append(Spacer(0, 2*mm))

    if overall_best:
        pill, sub, gap, strength, rk = overall_best
        story.append(Spacer(0, 2*mm))
        story.append(Paragraph("<b>Overall largest gap</b>", _styles()["H2Teal"]))
        story.append(Paragraph(
            f"{pill} → {sub} (Gap {gap:.1f}; Strength {strength:.1f}/25; rank {rk})",
            styles["BodyText"]
        ))
        story.append(Paragraph(
            "This represents the overall area where your answers and priorities are most out of alignment. "
            "It’s your best ‘first win’ to create momentum.",
            styles["BodyText"]
        ))

    doc = SimpleDocTemplate(
        doc_buf,
        pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=16*mm, bottomMargin=16*mm,
        title="Life Alignment Diagnostic Report",
        author="Life Alignment",
    )
    # on_page draws header/footer on all non-cover pages
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)


# ---------- public entry points (both supported) ----------

def build_pdf_report(data: dict) -> bytes:
    """Return a PDF as bytes (1-arg signature)."""
    buf = io.BytesIO()
    _build(buf, data)
    return buf.getvalue()


def build_pdf_report_from_payload(data: dict, out_pdf: io.BytesIO) -> None:
    """Write PDF into out_pdf (2-arg signature)."""
    _build(out_pdf, data)
