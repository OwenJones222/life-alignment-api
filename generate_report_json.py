# generate_report_json.py
import os, numpy as np, matplotlib.pyplot as plt
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image as RLImage

PILLAR_ORDER = ["Wealth", "Health", "Self", "Social"]
PILLAR_COLOURS = {"Wealth":"#FFD700","Health":"#5CB85C","Self":"#5BC0DE","Social":"#9B59B6"}
RANK_TO_WEIGHT = {1:4, 2:3, 3:2, 4:1}

def _tmp_path(name): 
    d = "/tmp/charts_tmp"; os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)

def _avg0to5(total25): 
    return round(total25/5.0, 2)

def _priority_gap_lists(raw_sub25, ranks):
    weights = [RANK_TO_WEIGHT.get(r, 2) for r in ranks]
    gap_raw = [(25 - v) * w for v, w in zip(raw_sub25, weights)]       # 0..100
    gap25   = [(25 - v) * (w/4.0) for v, w in zip(raw_sub25, weights)]  # 0..25
    return weights, gap_raw, gap25

def build_pdf_from_payload(payload: dict, out_pdf: str):
    answers = payload["answers"]  # dict pillar_key -> list of {qIndex,text,value}
    importance = payload.get("importance", {})  # optional (pillar_key -> 1..4)
    # Map keys like "health" to "Health", etc.
    norm = lambda k: k.strip().capitalize()

    # Build per-pillar aggregates
    pillar = {}
    all_sub_gaps = []
    for key_raw, items in answers.items():
        key = norm(key_raw)
        subs = []      # subtheme names
        raw25 = []     # totals per subtheme (0..25)
        # Expect 4 subthemes × 5 questions each, but support any count: group by qIndex//5
        # Derive subtheme labels from the first item text prefix up to "–" or use generic.
        groups = {}
        for it in items:
            idx = int(it.get("qIndex", 0))
            grp = idx // 5
            groups.setdefault(grp, []).append(it)
        for grp in sorted(groups.keys()):
            group_items = sorted(groups[grp], key=lambda x: x["qIndex"])
            subtotal = sum(int(x.get("value") or 0) for x in group_items)
            # name
            first_text = (group_items[0].get("text") or "").strip()
            name = first_text.split("–")[0].strip() if "–" in first_text else f"Theme {grp+1}"
            subs.append(name)
            raw25.append(subtotal)

        # pad/trim to 4
        while len(subs) < 4: 
            subs.append(f"Theme {len(subs)+1}"); raw25.append(0)
        subs = subs[:4]; raw25 = raw25[:4]

        # ranks (default 2 if missing)
        ranks = [int(importance.get(key_raw, 2))]*4  # per-pillar rank applied to all subthemes (MVP)
        weights, gap_raw, gap25 = _priority_gap_lists(raw25, ranks)

        total_raw100 = sum(raw25)
        avg_pillar_0to5 = round(total_raw100/20.0, 2)  # 20 q

        for i in range(4):
            all_sub_gaps.append({
                "pillar": key, "subtheme": subs[i], "raw25": raw25[i],
                "rank": ranks[i], "weight": weights[i],
                "gap_raw": gap_raw[i], "gap25": gap25[i],
            })

        pillar[key] = {
            "subs": subs, "raw25": raw25, "ranks": ranks, "weights": weights,
            "gap_raw": gap_raw, "gap25": gap25,
            "total_raw100": total_raw100, "avg0to5": avg_pillar_0to5,
            "raw_scaled50": total_raw100/100*50
        }

    # Radar (pillar strength 0–50)
    angles = np.linspace(0, 2*np.pi, 4, endpoint=False).tolist()
    angles += angles[:1]
    radar_vals = [pillar[p]["raw_scaled50"] for p in PILLAR_ORDER]
    radar_loop = radar_vals + radar_vals[:1]
    import matplotlib.pyplot as plt
    plt.figure(figsize=(6.8,6.8))
    ax = plt.subplot(111, polar=True)
    ax.plot(angles, radar_loop, linewidth=2, color="#444444")
    ax.fill(angles, radar_loop, alpha=0.25, color="#999999")
    ax.set_yticks([10,20,30,40,50]); ax.set_ylim(0,50)
    ax.set_xticks(angles[:-1]); ax.set_xticklabels(PILLAR_ORDER)
    for label, name in zip(ax.get_xticklabels(), PILLAR_ORDER):
        label.set_color(PILLAR_COLOURS[name])
    plt.title("Life Alignment Spiderweb (Pillar Strength 0–50)", fontsize=12, weight="bold")
    radar_path = _tmp_path("radar_strength.png")
    plt.savefig(radar_path, dpi=200, bbox_inches="tight"); plt.close()

    # Per-pillar bar charts
    bar_paths = {}
    for p in PILLAR_ORDER:
        subs = pillar[p]["subs"]; strength = pillar[p]["raw25"]; gap = pillar[p]["gap25"]
        ranks = pillar[p]["ranks"]; weights = pillar[p]["weights"]; col = PILLAR_COLOURS[p]
        x = np.arange(4); width = 0.38
        fig, ax = plt.subplots(figsize=(9,6))
        r1 = ax.bar(x - width/2, strength, width, label="Strength (0–25)", color=col, alpha=0.60)
        r2 = ax.bar(x + width/2, gap,      width, label="Priority Gap (0–25)", color=col, alpha=0.95)
        ax.set_ylim(0,25); ax.axhline(12, linestyle="--", linewidth=0.8, color="#666")
        ax.axhline(18, linestyle="--", linewidth=0.8, color="#666")
        xt = [f"{s}\n(rank {rk} → w{wt})" for s,rk,wt in zip(subs, ranks, weights)]
        ax.set_xticks(x); ax.set_xticklabels(xt)
        ax.set_ylabel("0–25 scale (higher Strength is better; higher Gap needs attention)")
        ax.set_title(f"{p} – Strength vs Priority Gap (rank 1 = most important)")
        ax.legend()
        for bars in (r1, r2):
            for b in bars:
                h = b.get_height()
                ax.annotate(f"{h:.1f}" if isinstance(h,float) else f"{int(h)}",
                            (b.get_x()+b.get_width()/2, h), textcoords="offset points",
                            xytext=(0,3), ha="center", fontsize=8)
        path = _tmp_path(f"{p}_bars.png"); plt.tight_layout()
        plt.savefig(path, dpi=200, bbox_inches="tight"); plt.close()
        bar_paths[p] = path

    # Top per pillar & overall
    pillar_top = {}
    for p in PILLAR_ORDER:
        idx = int(np.argmax(pillar[p]["gap_raw"]))
        pillar_top[p] = {
            "sub": pillar[p]["subs"][idx], "gap": pillar[p]["gap_raw"][idx],
            "raw": pillar[p]["raw25"][idx], "rank": pillar[p]["ranks"][idx],
            "wt": pillar[p]["weights"][idx],
        }
    max_row = max(all_sub_gaps, key=lambda r: r["gap_raw"]) if all_sub_gaps else None

    # PDF
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", fontSize=9))
    doc = SimpleDocTemplate(out_pdf, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    S = []

    # Title
    S += [Spacer(1,1.2*cm),
          Paragraph("<para align='center'><font size=20><b>Life Alignment Diagnostic</b></font></para>", styles["Title"]),
          Spacer(1,0.4*cm), Paragraph(f"<para align='center'>{datetime.now():%d %b %Y}</para>", styles["Normal"]),
          PageBreak()]

    # Radar
    S += [Paragraph("<b>Spiderweb Summary (Pillar Strength)</b>", styles["Heading1"]),
          RLImage(radar_path, width=15*cm, height=15*cm),
          PageBreak()]

    # Per-pillar pages
    for p in PILLAR_ORDER:
        S += [Paragraph(f"<font color='{PILLAR_COLOURS[p]}'><b>{p} Pillar</b></font>", styles["Heading1"]),
              RLImage(bar_paths[p], width=16*cm, height=9*cm),
              PageBreak()]

    # Priority Focus Summary (your wording)
    S.append(Paragraph("<b>Priority Focus Summary</b>", styles["Heading1"]))
    S.append(Paragraph("<b>Top focus in each pillar</b>", styles["Normal"]))
    for p in PILLAR_ORDER:
        t = pillar_top[p]
        line = f"<b>{p}</b> → <b>{t['sub']}</b> (Gap {t['gap']:.1f}; Strength {t['raw']}/25; rank {t['rank']}, weight {t['wt']})"
        S.append(Paragraph(line, styles["Small"]))
        S.append(Paragraph(
            (f"This represents the area within your <b>{p}</b> Pillar in which your individual answers, "
             f"and your overall priority given to <b>{t['sub']}</b>, are out of alignment. "
             "It is therefore the area to concentrate on first in order to make the biggest improvement for you."),
            styles["Small"]))
        S.append(Spacer(1, 0.15*cm))

    S.append(Spacer(1, 0.3*cm))
    S.append(Paragraph("<b>Overall largest gap</b>", styles["Normal"]))
    if max_row:
        p_name = max_row["pillar"]; sub = max_row["subtheme"]
        S.append(Paragraph(
            (f"<b>{p_name}</b> → <b>{sub}</b> "
             f"(Gap {max_row['gap_raw']:.1f}; Strength {max_row['raw25']}/25; "
             f"rank {max_row['rank']}, weight {max_row['weight']})"),
            styles["Small"]))
        S.append(Paragraph(
            (f"This represents the area within your <b>{p_name}</b> Pillar in which your individual answers, "
             f"and your overall priority given to <b>{sub}</b>, are out of alignment. "
             "It is therefore the area to concentrate on first in order to make the biggest improvement for you."),
            styles["Small"]))
    else:
        S.append(Paragraph("No ranked priorities detected; results reflect raw strengths only.", styles["Small"]))

    doc.build(S)


