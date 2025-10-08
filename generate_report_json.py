# -*- coding: utf-8 -*-
# Robust PDF builder for web payloads (per-subtheme labels, ranks above bars, wildcards)
#
# Public API:
#   build_pdf_report(payload: dict, out_pdf: str)

import os
import numpy as np
from datetime import datetime

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

# ---------- constants ----------
PILLAR_KEYS     = ["wealth", "health", "self", "social"]
PILLAR_ORDER    = ["Wealth", "Health", "Self", "Social"]
PILLAR_COLOURS  = {
    "wealth": "#FFD700",
    "health": "#5CB85C",
    "self":   "#5BC0DE",
    "social": "#9B59B6",
}
CHARTS_DIR = "charts_tmp"


# ---------- helpers ----------
def _safe_get(d, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def _pillar_meta(payload):
    """
    Returns: dict pillar_key -> {'label': str, 'subthemes': [4 names]}
    Accepts meta in multiple shapes, falls back to Theme 1..4.
    """
    result = {}
    meta_pillars = _safe_get(payload, "meta", "pillars", default=[]) or []
    if isinstance(meta_pillars, list):
        for item in meta_pillars:
            key   = str(item.get("key", "")).strip().lower()
            label = (item.get("label") or key.title() or "").strip()
            subs  = (item.get("subthemes") or item.get("subs") or [])
            if not isinstance(subs, list):
                subs = []
            while len(subs) < 4:
                subs.append(f"Theme {len(subs)+1}")
            subs = subs[:4]
            if key in PILLAR_KEYS:
                result[key] = {"label": label or key.title(), "subthemes": subs}

    for k, nice in zip(PILLAR_KEYS, PILLAR_ORDER):
        if k not in result:
            result[k] = {"label": nice, "subthemes": [f"Theme {i}" for i in range(1,5)]}
    return result

def _read_ranks(payload, pillar_key):
    """
    Returns 4 integers 1..4 (1 = most important). Tolerant to strings/None.
    """
    arr = _safe_get(payload, "importance", pillar_key, default=None)
    if isinstance(arr, list) and len(arr) == 4:
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
    out, i = [], 0
    for _ in range(chunks):
        part = vals[i:i+size]
        out.append(sum(v for v in part if isinstance(v, (int, float))))
        i += size
    while len(out) < 4:
        out.append(0)
    return out[:4]

def _read_sub_scores(payload, pillar_key):
    """
    Returns 4 numbers (0..25) — tolerant to several field names:
      subtotals[p], subtheme_totals[p], sub_scores[p], scores[p],
      pillars[p].subs, or group ratings[p] by 5.
    """
    for path in [
        ("subtotals", pillar_key),
        ("subtheme_totals", pillar_key),
        ("sub_scores", pillar_key),
        ("scores", pillar_key),
    ]:
        arr = _safe_get(payload, *path, default=None)
        if isinstance(arr, list) and len(arr) >= 4:
            return [float(arr[i]) if i < len(arr) else 0.0 for i in range(4)]

    arr = _safe_get(payload, "pillars", pillar_key, "subs", default=None)
    if isinstance(arr, list) and len(arr) >= 4:
        return [float(arr[i]) if i < len(arr) else 0.0 for i in range(4)]

    arr = _safe_get(payload, "ratings", pillar_key, default=None)
    if isinstance(arr, list) and len(arr) >= 20:
        return _chunk_sum(arr, size=5, chunks=4)

    return [0.0, 0.0, 0.0, 0.0]

def _weights_from_ranks(ranks):
    # 1 = most important -> adjusted 4; 4 -> 1
    adjusted = [5 - r for r in ranks]
    mean_adj = np.mean(adjusted) if adjusted else 1.0
    if not mean_adj:
        mean_adj = 1.0
    return [a / mean_adj for a in adjusted]

def _largest_gap(pillars_dict):
    best = ("", -1, -1.0)
    for pk, rec in pillars_dict.items():
        raw, fac = rec["raw"], rec["factors"]
        for i in range(4):
            gap = fac[i] * max(0, 25 - raw[i])
            if gap > best[2]:
                best = (pk, i, gap)
    return best

def _read_wildcards(payload, pillar_key):
    """
    Returns a list of (question, answer) for a pillar.
    Accepts:
      payload["wildcards"][pillar] -> list of {'q','a'} or {'question','answer'} or strings
      legacy: payload['wild_health'], etc. (single string)
    """
    out = []

    wc = _safe_get(payload, "wildcards", pillar_key, default=None)
    if isinstance(wc, list):
        for item in wc:
            if isinstance(item, dict):
                q = item.get("q") or item.get("question") or ""
                a = item.get("a") or item.get("answer") or ""
                if q or a:
                    out.append((str(q).strip(), str(a).strip()))
            elif isinstance(item, str) and item.strip():
                out.append(("", item.strip()))

    legacy_key = {
        "health": "wild_health",
        "wealth": "wild_wealth",
        "self":   "wild_self",
        "social": "wild_social",
    }[pillar_key]
    legacy_val = payload.get(legacy_key)
    if isinstance(legacy_val, str) and legacy_val.strip():
        out.append(("", legacy_val.strip()))

    return out


# ---------- plotting ----------
def _bar_chart(pillar_label, pillar_key, sub_names, raw, wtd, ranks):
    """
    Draws bars and places 'rank N' ABOVE each subtheme pair.
    Sub-theme labels are the x-tick labels (below bars).
    """
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

    # Value labels on the bars
    for bars in (r1, r2):
        for b in bars:
            h = b.get_height()
            ax.annotate(f"{h:.0f}", (b.get_x()+b.get_width()/2, h),
                        textcoords="offset points", xytext=(0,3),
                        ha="center", fontsize=8)

    # --- Rank labels ABOVE each pair (no weights anywhere) ---
    for i in range(4):
        top = max(raw[i], wtd[i])
        ax.text(x[i], top + 1.2, f"rank {ranks[i]}", ha="center", va="bottom", fontsize=9, fontweight="bold")

    os.makedirs(CHARTS_DIR, exist_ok=True)
    path = os.path.join(CHARTS_DIR, f"{pillar_key}_bars.png")
    plt.tight_layout()
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    return path


# ---------- main entry ----------
def build_pdf_report(payload: dict, out_pdf: str):
    os.makedirs(CHARTS_DIR, exist_ok=True)

    # Resolve labels/meta
    meta = _pillar_meta(payload)

    # Build pillar structures
    pillars = {}
    for key in PILLAR_KEYS:
        label     = meta[key]["label"]
        sub_names = meta[key]["subthemes"]
        raw       = _read_sub_scores(payload, key)          # 4 numbers 0..25
        ranks     = _read_ranks(payload, key)               # 4 ints 1..4
        factors   = _weights_from_ranks(ranks)
        wtd       = [min(25.0, round(raw[i]*factors[i], 1)) for i in range(4)]
        wild      = _read_wildcards(payload, key)           # [(q,a),...]

        pillars[key] = {
            "label": label, "sub_names": sub_names,
            "raw": raw, "ranks": ranks, "factors": factors, "wtd": wtd,
            "raw_total": sum(raw),
            "wtd_total": sum(wtd),
            "wtd_scaled": (sum(wtd) / 100.0) * 50.0,
            "chart": _bar_chart(label, key, sub_names, raw, wtd, ranks),
            "wild": wild,
        }

    # Spiderweb of weighted (0..50)
    angles = np.linspace(0, 2*np.pi, 4, endpoint=False).tolist()
    angles += angles[:1]
    radar_vals = [pillars[k]["wtd_scaled"] for k in PILLAR_KEYS]
    loop = radar_vals + radar_vals[:1]

    plt.figure(figsize=(6.8, 6.8))
    ax = plt.subplot(111, polar=True)
    ax.plot(angles, loop, linewidth=2, color="#444444")
    ax.fill(angles, loop, alpha=0.25, color="#999999")
    ax.set_yticks([10,20,30,40,50])
    ax.set_ylim(0, 50)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels([pillars[k]["label"] for k in PILLAR_KEYS])
    for lbl, k in zip(ax.get_xticklabels(), PILLAR_KEYS):
        lbl.set_color(PILLAR_COLOURS.get(k, "#333"))
    plt.title("Life Alignment Spiderweb (Weighted by Your Priorities — 1 = most important)",
              fontsize=12, weight="bold")
    radar_path = os.path.join(CHARTS_DIR, "radar_weighted_colour_flipped.png")
    plt.savefig(radar_path, dpi=200, bbox_inches="tight")
    plt.close()

    # Largest gap across everything
    lg_key, lg_idx, lg_gap = _largest_gap(pillars)

    # ---------- Build PDF ----------
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", fontSize=9))

    doc = SimpleDocTemplate(
        out_pdf,
        pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )
    story = []

    # Cover
    date_str = datetime.now().strftime("%d %b %Y")
    story += [
        Spacer(1, 1.2*cm),
        Paragraph("<para align='center'><font size=20><b>Life Alignment Diagnostic</b></font></para>", styles["Title"]),
        Spacer(1, 0.5*cm),
        Paragraph(f"<para align='center'>{date_str}</para>", styles["Normal"]),
        Spacer(1, 0.5*cm),
        PageBreak()
    ]

    # Spiderweb page
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
            p["label"], f"{p['raw_total']:.0f}", f"{p['wtd_total']:.1f}", f"{p['wtd_scaled']:.1f}", ranks_text
        ])
    t = Table(table_data, colWidths=[5.5*cm, 3.5*cm, 3.5*cm, 3.5*cm, 4*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("BOX", (0,0), (-1,-1), 0.25, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.black),
    ]))
    story += [t, PageBreak()]

    # Per-pillar pages
    for k in PILLAR_KEYS:
        p   = pillars[k]
        col = PILLAR_COLOURS.get(k, "#333333")

        story += [
            Paragraph(f"<font color='{col}'><b>{p['label']} Pillar</b></font>", styles["Heading1"]),
            RLImage(p["chart"], width=16*cm, height=9*cm),
            Spacer(1, 0.2*cm)
        ]

        # (Ranks paragraph removed – ranks are printed above the bars)

        # Wildcards (if any)
        if p["wild"]:
            story.append(Paragraph("<b>Wildcard reflections (not scored):</b>", styles["Normal"]))
            for q, a in p["wild"]:
                if q:
                    story.append(Paragraph(f"<b>Q:</b> {q}", styles["Small"]))
                if a:
                    story.append(Paragraph(f"<b>A:</b> {a}", styles["Small"]))
            story.append(Spacer(1, 0.2*cm))

        story.append(PageBreak())

    # Priority focus summary (largest single gap)
    story.append(Paragraph("<b>Priority Focus Summary</b>", styles["Heading1"]))
    if lg_key in pillars and 0 <= lg_idx < 4:
        best = pillars[lg_key]
        story.append(Paragraph(
            f"Your largest <b>priority gap</b> is in <b>{best['label']} → {best['sub_names'][lg_idx]}</b>: "
            f"Strength {best['raw'][lg_idx]:.0f}/25; rank {best['ranks'][lg_idx]} (1 = most important). "
            "This area appears both highly valued and currently under-supported — "
            "focus here first for the fastest gains.",
            styles["Normal"]
        ))
    else:
        story.append(Paragraph("No ranked priorities detected; results reflect raw scores only.", styles["Normal"]))

    doc.build(story)
