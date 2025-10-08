# generate_report_json.py
import io
from typing import Dict, List, Tuple

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
from reportlab.lib import colors

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ---------- helpers ----------

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


def _draw_pillar_chart(
    pillar_label: str,
    subtheme_labels: List[str],
    strengths: List[float],
    gaps: List[float],
    ranks: List[int],
) -> bytes:
    """Return PNG bytes of the chart."""
    fig, ax = plt.subplots(figsize=(7.8, 5.0), dpi=150)  # fits A4 nicely

    x = range(len(subtheme_labels))
    bar1 = ax.bar(x, strengths, width=0.35, label="Strength (0–25)", color="#69b37b")
    bar2 = ax.bar([i + 0.35 for i in x], gaps, width=0.35, label="Priority Gap (0–25)", color="#9ad0a9")

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


# ---------- main PDF builder ----------

def _build(doc_buf: io.BytesIO, data: dict) -> None:
    styles = getSampleStyleSheet()
    story = []

    meta = data.get("meta", {})
    pillars_meta = meta.get("pillars", [])
    answers = data.get("answers", {})
    ranks_by_pillar = data.get("importance", {})
    wild = data.get("wildcards", {}) or {}

    slices = _pillar_slices()

    # Title
    story.append(_heading("Life Alignment Diagnostic Report", level=1))
    story.append(Spacer(0, 6*mm))

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
        gaps = [(25 - s) * _rank_to_scale(rank) for s, rank in zip(st, ranks_arr)]

        # Chart image
        img_bytes = _draw_pillar_chart(pillar_label, subtheme_labels, st, gaps, ranks_arr)
        img = Image(io.BytesIO(img_bytes))
        img._restrictSize(180*mm, 110*mm)

        # Pillar heading + chart
        story.append(_heading(f"{pillar_label} Pillar", level=1))
        story.append(Spacer(0, 2*mm))
        story.append(img)
        story.append(Spacer(0, 2*mm))

        # Rank line under chart
        rank_pairs = ", ".join([f"{lbl}: {rk}" for lbl, rk in zip(subtheme_labels, ranks_arr)])
        story.append(_paragraph(
            f"<b>Participant importance ranks (1 = most important):</b> {rank_pairs}"
        ))
        story.append(Spacer(0, 2*mm))

        # Wildcards for this pillar (show any that match this key)
        # Expecting keys like wild_health_1..5 etc.
        story.append(_paragraph("<b>Wildcard reflections (not scored):</b>"))
        for i in range(1, 6):
            wkey = f"wild_{key}_{i}"
            if wkey in wild and str(wild[wkey]).strip():
                story.append(_paragraph(f"• {wild[wkey]}"))
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
    story.append(_heading("Priority Focus Summary", level=1))
    story.append(Spacer(0, 3*mm))
    if per_pillar_focus:
        for (pill, sub, gap, strength, rk) in per_pillar_focus:
            story.append(_paragraph(
                f"<b>{pill} → {sub}</b> "
                f"(Gap {gap:.1f}; Strength {strength:.1f}/25; rank {rk})"
            ))
            story.append(_paragraph(
                f"This represents the area within your <b>{pill}</b> Pillar in which your "
                f"individual answers and your stated priority given to <b>{sub}</b> are out of alignment. "
                "It’s therefore a strong candidate to focus on first for the biggest impact."
            ))
            story.append(Spacer(0, 2*mm))

    if overall_best:
        pill, sub, gap, strength, rk = overall_best
        story.append(Spacer(0, 2*mm))
        story.append(_paragraph(
            f"<b>Overall largest gap</b><br/>"
            f"{pill} → {sub} (Gap {gap:.1f}; Strength {strength:.1f}/25; rank {rk})"
        ))
        story.append(_paragraph(
            "This represents the overall area where your answers and priorities are most out of alignment. "
            "It’s your best ‘first win’ to create momentum."
        ))

    doc = SimpleDocTemplate(
        doc_buf,
        pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=16*mm, bottomMargin=16*mm,
        title="Life Alignment Diagnostic Report",
        author="Life Alignment",
    )
    doc.build(story)


# ---------- public entry points (both supported) ----------

def build_pdf_report(data: dict) -> bytes:
    """Return a PDF as bytes (1-arg signature)."""
    buf = io.BytesIO()
    _build(buf, data)
    return buf.getvalue()


def build_pdf_report_from_payload(data: dict, out_pdf: io.BytesIO) -> None:
    """Write PDF into out_pdf (2-arg signature)."""
    _build(out_pdf, data)
