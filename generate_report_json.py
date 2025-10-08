# -*- coding: utf-8 -*-
# Robust PDF builder for web payloads (per-subtheme labels + ranks)
#
# Exposes: build_pdf_report(payload: dict, out_pdf: str)
#
# Tolerant payload reading:
# - Subtheme labels: payload["meta"]["pillars"][{key,label,subthemes}]
# - Ranks per subtheme: payload["importance"][pillar] -> [1..4] (1 = most important)
# - Subtheme scores (0..25 each x4) from first available:
#     payload["subtotals"][pillar]
#     payload["scores"][pillar]
#     payload["pillars"][pillar]["subs"]
#     group payload["ratings"][pillar] into chunks of 5 (sum)
#
# Keeps your existing visual style, but replaces "Theme 1..4" with real names
# and uses the submitted ranks in both charts and the Priority Focus Summary.

import os
import math
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image as RLImage
)

# --------- Constants ----------
PILLAR_ORDER = ["Wealth", "Health", "Self", "Social"]
PILLAR_KEYS   = ["wealth", "health", "self", "social"]
PILLAR_COLOURS = {
    "wealth": "#FFD700",
    "health": "#5CB85C",
    "self":   "#5BC0DE",
    "social": "#9B59B6",
}
CHARTS_DIR = "charts_tmp"


# --------- Helpers to read tolerant payload ----------
def _safe_get(dct, *keys, default=None):
    cur = dct
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def _pillar_meta(payload):
    """Return dict pillar_key -> {'label': str, 'subthemes': [str,str,str,str]}"""
    result = {}
    meta_pillars = _safe_get(payload, "meta", "pillars", default=[])
    if isinstance(meta_pillars, list):
        for item in meta_pillars:
            key   = str(item.get("key", "")).strip().lower()
            label = str(item.get("label", "")).strip() or key.title()
            subs  = item.get("subthemes") or []
            if not isinstance(subs, list):
                subs = []
            # pad to 4
            while len(subs) < 4:
                subs.append(f"Theme {len(subs)+1}")
            subs = subs[:4]
            if key in PILLAR_KEYS:
                result[key] = {"label": label, "subthemes": subs}
    # supply defaults where missing
    for k, nice in zip(PILLAR_KEYS, PILLAR_ORDER):
        if k not in result:
            result[k] = {"label": nice, "subthemes": [f"Theme {i}" for i in range(1,5)]}
    return result

def _read_ranks(payload, pillar_key):
    """Return list of 4 ranks (1..4). If invalid/missing, return [2,2,2,2]."""
    arr = _safe_get(payload, "importance", pillar_key, default=None)
    if isinstance(arr, list) and len(arr) == 4 and all(isinstance(x, int) for x in arr):
        # Coerce to 1..4 if possible
        cleaned = []
        for x in arr:
            try:
                xi = int(x)
            except Exception:
                xi = 2
            cleaned.append(max(1, min(4, xi)))
        return cleaned
    return [2, 2, 2, 2]

def _chunk_sum(vals, size=5, chunks=4):
    out = []
    i = 0
    for _ in range(chunks):
        part = vals[i:i+size]
        out.append(sum(x for x in part if isinstance(x, (int, float))))
        i += size
    while len(out) < 4:
        out.append(0)
    return out[:4]

def _read_sub_scores(payload, pillar_key):
    """
    Return 4 numbers (0..25 each). Tries multiple shapes:
    - payload.subtotals[pillar]
    - payload.scores[pillar]
    - payload.pillars[pillar].subs
    - group payload.ratings[pillar] by 5
    """
    # 1) subtotals
    arr = _safe_get(payload, "subtotals", pillar_key, default=None)
    if isinstance(arr, list) and len(arr) >= 4:
        return [float(arr[i]) if i < len(arr) else 0.0 for i in range(4)]

    # 2) scores
    arr = _safe_get(payload, "scores", pillar_key, default=None)
    if isinstance(arr, list) and len(arr) >= 4:
        return [float(arr[i]) if i < len(arr) else 0.0 for i in range(4)]

    # 3) pillars[pillar].subs
    arr = _safe_get(payload, "pillars", pillar_key, "subs", default=None)
    if isinstance(arr, list) and len(arr) >= 4:
        return [float(arr[i]) if i < len(arr) else 0.0 for i in range(4)]

    # 4) ratings[pillar] -> group by 5
    arr = _safe_get(payload, "ratings", pillar_key, default=None)
    if isinstance(arr, list) and len(arr) >= 20:
        return _chunk_sum(arr, size=5, chunks=4)

    # Fallback
    return [0.0, 0.0, 0.0, 0.0]


# --------- Core weighting / summary ----------
def _weights_from_ranks(ranks):
    # 1 = most important -> adjusted 4; 4 = least -> adjusted 1
    adjusted = [5 - r for r in ranks]
    mean_adj = np.mean(adjusted) if adjusted else 1.0
    if mean_adj == 0:
        mean_adj = 1.0
    return [a / mean_adj for a in adjusted]

def _largest_gap(pillars_dict):
    """
    pillars_dict: pillar_key -> {
        'sub_names': [...],
        'raw': [...],
        'ranks': [...],
        'factors': [...]
    }
    Returns (pillar_key, idx, gap_value)
    """
    best = ("", -1, -1.0)
    for pk, rec in pillars_dict.items():
        raw = rec["raw"]
        fac = rec["factors"]
        for i in range(4):
            gap = fac[i] * max(0, 25 - raw[i])
            if gap > best[2]:
                best = (pk, i, gap)
    return best


# --------- Plotting ----------
def _bar_chart(pillar_label, pillar_key, sub_names, raw, wtd, ranks):
    col = PILLAR_COLOURS.get(pillar_key, "#999999")
    x = np.arange(4)
    width = 0.38

    fig, ax = plt.subplots(figsize=(8.8, 5.2))
    r1 = ax.bar(x - width/2, raw, width, label="Strength (0–25)", color=col, alpha=0.45)
    r2 = ax.bar(x + width/2, wtd, width, label="Priority Gap (0–25)", color=col, alpha=0.95)

    ax.set_ylim(0, 25)
    ax.axhline(12, linestyle="--", linewidth=0.8, color="#666666")
    ax.axhline(18, linestyle="--", linewidth=0.8, color="#666666")
    ax.set_xticks(x)
    ax.set_xticklabels(sub_names, rotation=12, ha="right")
    ax.set_ylabel("0–25 scale\n(higher Strength is better; higher Gap needs attention)")
    ax.set_title(f"{pillar_label} – Strength vs Priority Gap (rank 1 = most important)")
    ax.legend(loc="upper right")

    for bars in (r1, r2):
        for b in bars:
            h = b.get_height()
            ax.annotate(f"{h:.0f}",
                        (b.get_x()+b.get_width()/2, h),
                        textcoords="offset points", xytext=(0,3),
                        ha="center", fontsize=8)

    # Show the rank→weight line under the axis
    adjusted = [5 - r for r in ranks]
    mean_adj = np.mean(adjusted) if adjusted else 1.0
    for xi, rk in zip(x, ranks):
        factor = (5 - rk)/mean_adj if mean_adj else 1.0
        ax.text(xi, -1.7, f"rank {rk} → w{factor:.2f}", ha="center", va="top", fontsize=8)

    path = os.path.join(CHARTS_DIR, f"{pillar_key}_bars.png")
    plt.tight_layout()
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    return path


# --------- Public entry point ----------
def build_pdf_report(payload: dict, out_pdf: str):
    os.makedirs(CHARTS_DIR, exist_ok=True)

    # Resolve labels/meta
    meta = _pillar_meta(payload)  # pillar_key -> {'label','subthemes'}

    # Build pillar data
    pillars = {}
    for key in PILLAR_KEYS:
        label     = meta[key]["label"]
        sub_names = meta[key]["subthemes"]
        raw       = _read_sub_scores(payload, key)              # 4 numbers 0..25
        ranks     = _read_ranks(payload, key)                   # 4 ints 1..4
        factors   = _weights_from_ranks(ranks)
        wtd       = [min(25.0, round(raw[i]*factors[i], 1)) for i in range(4)]

        pillars[key] = {
            "label": label,
            "sub_names": sub_names,
            "raw": raw,
            "ranks": ranks,
            "factors": factors,
            "wtd": wtd,
            "raw_total": sum(raw),
            "wtd_total": sum(wtd),
            "wtd_scaled": (sum(wtd) / 100.0) * 50.0,
            "chart": _bar_chart(label, key, sub_names, raw, wtd, ranks),
        }

    # Radar (weighted, 0..50 per pillar)
    angles = np.linspace(0, 2*np.pi, 4, endpoint=False).tolist()
    angles += angles[:1]
    radar_vals = [pillars[k]["wtd_scaled"] for k in PILLAR_KEYS]
    radar_loop = radar_vals + radar_vals[:1]

    plt.figure(figsize=(6.8, 6.8))
    ax = plt.subplot(111, polar=True)
    ax.plot(angles, radar_loop, linewidth=2, color="#444444")
    ax.fill(angles, radar_loop, alpha=0.25, color="#999999")
    ax.set_yticks([10, 20, 30, 40, 50])
    ax.set_ylim(0, 50)
    tick_labels = [pillars[k]["label"] for k in PILLAR_KEYS]
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(tick_labels)
    for label_txt, key in zip(ax.get_xticklabels(), PILLAR_KEYS):
        label_txt.set_color(PILLAR_COLOURS.get(key, "#333333"))
    plt.title("Life Alignment Spiderweb (Weighted by Your Priorities — 1 = most important)",
              fontsize=12, weight="bold")
    radar_path = os.path.join(CHARTS_DIR, "radar_weighted_colour_flipped.png")
    plt.savefig(radar_path, dpi=200, bbox_inches="tight")
    plt.close()

    # Largest gap overall
    lg_key, lg_idx, lg_gap = _largest_gap(pillars)

    # --------- Build PDF ----------
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", fontSize=9))

    doc = SimpleDocTemplate(
        out_pdf,
        pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )
    story = []

    # Title
    date_str = datetime.now().strftime("%d %b %Y")
    story += [
        Spacer(1, 1.2*cm),
        Paragraph("<para align='center'><font size=20><b>Life Alignment Diagnostic</b></font></para>", styles["Title"]),
        Spacer(1, 0.5*cm),
        Paragraph(f"<para align='center'>{date_str}</para>", styles["Normal"]),
        Spacer(1, 0.5*cm),
        PageBreak()
    ]

    # Spider
    story += [
        Paragraph("<b>Spiderweb Summary (Weighted by Your Priorities — 1 = most important)</b>", styles["Heading1"]),
        RLImage(radar_path, width=15*cm, height=15*cm),
        Spacer(1, 0.2*cm)
    ]

    # Overview table
    table_data = [["Pillar", "Raw Total (0–100)", "Weighted Total (0–100)", "Weighted Scaled (0–50)", "Ranks used (1=most)"]]
    for k in PILLAR_KEYS:
        p = pillars[k]
        ranks_text = ", ".join(str(r) for r in p["ranks"])
        table_data.append([
            p["label"],
            f"{p['raw_total']:.0f}",
            f"{p['wtd_total']:.1f}",
            f"{p['wtd_scaled']:.1f}",
            ranks_text
        ])
    t = Table(table_data, colWidths=[5.5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 4*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("BOX", (0,0), (-1,-1), 0.25, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.black),
    ]))
    story += [t, PageBreak()]

    # Per pillar pages
    for k in PILLAR_KEYS:
        p = pillars[k]
        col = PILLAR_COLOURS.get(k, "#333333")
        story += [
            Paragraph(f"<font color='{col}'><b>{p['label']} Pillar</b></font>", styles["Heading1"]),
            RLImage(p["chart"], width=16*cm, height=9*cm),
            Spacer(1, 0.2*cm)
        ]

        # Ranks with subtheme names
        rank_lines = [f"{name}: {rk}" for name, rk in zip(p["sub_names"], p["ranks"])]
        story += [
            Paragraph(f"<i>Importance ranks (1 = most important):</i> " + ", ".join(rank_lines), styles["Small"]),
            Spacer(1, 0.2*cm)
        ]

        # (Optional) You can list wildcard reflections here if you include them in payload
        story.append(PageBreak())

    # Summary with largest gap
    story.append(Paragraph("<b>Priority Focus Summary</b>", styles["Heading1"]))
    if lg_key in pillars and 0 <= lg_idx < 4:
        best = pillars[lg_key]
        pillar_label = best["label"]
        sub_name     = best["sub_names"][lg_idx]
        raw_s        = best["raw"][lg_idx]
        rank_s       = best["ranks"][lg_idx]
        story.append(Paragraph(
            f"Your largest <b>priority gap</b> is in <b>{pillar_label} → {sub_name}</b>: "
            f"Strength {raw_s:.0f}/25, ranked {rank_s} (1 = most important). "
            f"This area appears both highly valued and currently under-supported — "
            f"it is the most likely place where focus pays off fastest.",
            styles["Normal"]
        ))
    else:
        story.append(Paragraph(
            "No ranked priorities detected; results reflect raw scores only.",
            styles["Normal"]
        ))

    doc.build(story)
