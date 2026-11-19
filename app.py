import io
import re
import textwrap
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.backends.backend_pdf import PdfPages

st.set_page_config(
    page_title="Archétype Entreprise — DAF externalisé & Pilotage BFR",
    page_icon="🏢",
    layout="wide"
)

# ==========================================
# 1. RÉFÉRENTIEL & SEUILS PME (Comptabilité Commerciale / Banque de France)
# ==========================================
def get_normes_pme(secteur):
    if "Service" in secteur:
        return {"type_court": "Services & Tertiaire", "bfr_jours": 30, "dso_max": 45, "dpo_min": 30, "dio_max": 0, "mcv_cible": 75.0, "ebe_cible": 12.0, "levier_dette_max": 2.5}
    elif "Négoce" in secteur or "Commerce" in secteur:
        return {"type_court": "Commerce & Négoce", "bfr_jours": 60, "dso_max": 30, "dpo_min": 45, "dio_max": 60, "mcv_cible": 35.0, "ebe_cible": 8.0, "levier_dette_max": 3.5}
    elif "Industrie" in secteur:
        return {"type_court": "Industrie & Production", "bfr_jours": 75, "dso_max": 60, "dpo_min": 50, "dio_max": 75, "mcv_cible": 45.0, "ebe_cible": 15.0, "levier_dette_max": 3.0}
    elif "BTP" in secteur:
        return {"type_court": "BTP & Construction", "bfr_jours": 45, "dso_max": 75, "dpo_min": 60, "dio_max": 30, "mcv_cible": 50.0, "ebe_cible": 10.0, "levier_dette_max": 2.5}
    elif "Hôtellerie" in secteur:
        return {"type_court": "Hôtellerie & Restauration (CHR)", "bfr_jours": -10, "dso_max": 5, "dpo_min": 40, "dio_max": 15, "mcv_cible": 68.0, "ebe_cible": 15.0, "levier_dette_max": 4.0}
    else:
        return {"type_court": "Transverse PME", "bfr_jours": 45, "dso_max": 45, "dpo_min": 45, "dio_max": 30, "mcv_cible": 50.0, "ebe_cible": 10.0, "levier_dette_max": 3.0}

def afficher_synthese_unique(titre, constat, points_appui, leviers_budget):
    st.markdown("---")
    with st.container(border=True):
        st.markdown(f"### Synthèse Stratégique & Clés de décision — {titre}")
        st.markdown(f"**1. Diagnostic Financier :** {constat}")
        st.markdown(f"**2. Force & Vulnérabilité (Points d'appui) :** {points_appui}")
        st.markdown(f"**3. Arbitrages & Recommandations DG :** {leviers_budget}")

def afficher_aide_pedagogique_onglet(titre_aide, points_cles):
    with st.expander(f"💡 DAF Pédagogique — {titre_aide}", expanded=False):
        for notion, explication in points_cles:
            st.markdown(f"* **{notion} :** {explication}")

# ==========================================
# 2. MOTEUR D'INGESTION FEC / BALANCE PME
# ==========================================
def _read_tabular_file(file_obj):
    name = getattr(file_obj, "name", "balance.csv")
    raw = file_obj.getvalue() if hasattr(file_obj, "getvalue") else file_obj.read()
    if name.lower().endswith((".xlsx", ".xls")):
        try: return pd.read_excel(io.BytesIO(raw))
        except Exception: return None
    for enc in ("utf-8", "latin-1", "cp1252"):
        for sep in (";", ",", "\t"):
            try:
                tmp = pd.read_csv(io.BytesIO(raw), sep=sep, encoding=enc)
                if tmp.shape[1] >= 2: return tmp
            except Exception: continue
    return None

def _to_num(series):
    return pd.to_numeric(series.astype(str).str.replace(r"[\xa0\s]", "", regex=True).str.replace(",", ".", regex=False), errors="coerce").fillna(0.0)

def parse_balance_pme(file_obj):
    name = getattr(file_obj, "name", "balance_pme_2025.csv")
    ym = re.search(r"(20[1-3]\d)", name)
    annee = ym.group(1) if ym else None
    df = _read_tabular_file(file_obj)
    if df is None or df.empty: return None

    cols_l = {c: str(c).strip().lower() for c in df.columns}
    compte_col = next((c for c, cl in cols_l.items() if any(k in cl for k in ("compte", "article", "numero", "numéro"))), df.columns[0])
    lib_col = next((c for c, cl in cols_l.items() if "libell" in cl or "intitul" in cl), None)
    deb_col = next((c for c, cl in cols_l.items() if "débit" in cl or "debit" in cl), None)
    cred_col = next((c for c, cl in cols_l.items() if "crédit" in cl or "credit" in cl), None)
    solde_col = next((c for c, cl in cols_l.items() if "solde" in cl or "montant" in cl), None)

    raw_c = df[compte_col].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    debits = _to_num(df[deb_col]) if deb_col else (_to_num(df[solde_col]).abs() if solde_col else pd.Series(0.0, index=df.index))
    credits = _to_num(df[cred_col]) if cred_col else (_to_num(df[solde_col]).abs() if solde_col else pd.Series(0.0, index=df.index))

    valid = raw_c.str.match(r"^\d{2,}$")
    if lib_col:
        valid = valid & ~df[lib_col].astype(str).str.lower().str.contains(r"\b(total|totaux)\b", regex=True)

    c_val, d_val, cr_val = raw_c[valid], debits[valid], credits[valid]
    norm_c = c_val.apply(lambda c: c.rstrip("0") if len(c.rstrip("0")) >= 2 else c[:2]) if (c_val.str.len().nunique() == 1 and c_val.str.len().iloc[0] >= 5) else c_val
    uniques = set(norm_c.unique())
    parents = {c for c in uniques if any(o != c and o.startswith(c) for o in uniques)}
    is_leaf = ~norm_c.isin(parents)
    comptes, debits, credits = c_val[is_leaf], d_val[is_leaf], cr_val[is_leaf]

    def sum_p(pref, side="debit"):
        m = comptes.apply(lambda x: any(x.startswith(p) for p in pref))
        return float(debits[m].sum() if side == "debit" else credits[m].sum())

    ca_70 = sum_p(["70"], "credit")
    prod_stockee_71 = sum_p(["71"], "credit") - sum_p(["71"], "debit")
    prod_immo_72 = sum_p(["72"], "credit")
    subv_74 = sum_p(["74"], "credit")
    aut_p = max(0.0, sum_p(["75", "78", "79"], "credit"))
    tp = ca_70 + prod_stockee_71 + prod_immo_72 + subv_74 + aut_p

    ach_60 = sum_p(["60"], "debit")
    var_stock_603 = sum_p(["603"], "debit") - sum_p(["603"], "credit")
    ext_61_62 = sum_p(["61", "62"], "debit")
    imp_63 = sum_p(["63"], "debit")
    ms_64 = sum_p(["64"], "debit")
    aut_65 = sum_p(["65"], "debit")
    fin_66 = sum_p(["66"], "debit")
    amo_681 = sum_p(["681"], "debit")
    amo_exc_68 = sum_p(["686", "687"], "debit")
    is_69 = sum_p(["69"], "debit")
    tc = ach_60 + var_stock_603 + ext_61_62 + imp_63 + ms_64 + aut_65 + fin_66 + amo_681 + amo_exc_68 + is_69

    if tp <= 0 and tc <= 0: return None

    stock_3 = sum_p(["3"], "debit") - sum_p(["3"], "credit")
    client_41 = sum_p(["41"], "debit") - sum_p(["41"], "credit")
    fourn_40 = sum_p(["40"], "credit") - sum_p(["40"], "debit")
    etat_44 = (sum_p(["44"], "debit") - sum_p(["44"], "credit")) * -1.0
    fp_10_12 = sum_p(["10", "11", "12"], "credit") - sum_p(["10", "11", "12"], "debit")
    dette_16 = sum_p(["16"], "credit") - sum_p(["16"], "debit")
    tres_51 = sum_p(["50", "512", "514", "53"], "debit") - sum_p(["50", "512", "514", "519"], "credit")

    rn = tp - tc
    caf = rn + amo_681 + amo_exc_68

    return {
        "fichier": name, "annee": annee,
        "ca_70": ca_70, "prod_stockee_71": prod_stockee_71, "prod_immo_72": prod_immo_72, "subv_74": subv_74, "aut_p": aut_p, "tp": tp,
        "ach_60": ach_60, "var_stock_603": var_stock_603, "ext_61_62": ext_61_62, "imp_63": imp_63, "ms_64": ms_64, "aut_65": aut_65,
        "fin_66": fin_66, "amo_681": amo_681, "is_69": is_69, "tc": tc,
        "rn": rn, "caf": caf,
        "stock_3": max(0.0, stock_3), "client_41": max(0.0, client_41), "fourn_40": max(0.0, fourn_40), "etat_44": etat_44,
        "fp_10_12": fp_10_12, "dette_16": max(0.0, dette_16), "tres_51": tres_51
    }

# ==========================================
# 3. GÉNÉRATEURS DE RAPPORTS PDF EXÉCUTIFS
# ==========================================
def _box(ax, x, y, w, h, fc="#ffffff", ec="#cbd5e1", lw=1.0, r=0.012):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", facecolor=fc, edgecolor=ec, linewidth=lw, transform=ax.transAxes))

def _draw_lines(ax, x, y_start, items, wrap_w=115, step_y=0.016, gap_item=0.005, fontsize=7.0, color="#1e293b", bold_prefix=False):
    y = y_start
    for it in items:
        wrapped = textwrap.wrap(it, width=wrap_w)
        for idx, wl in enumerate(wrapped):
            ax.text(x, y, wl, fontsize=fontsize, color=color, fontweight=("bold" if (bold_prefix and idx == 0) else "normal"), transform=ax.transAxes)
            y -= step_y
        y -= gap_item
    return y

def _header_pdf(ax, titre_doc, sous_titre):
    ax.add_patch(Rectangle((0, 0.915), 1, 0.085, facecolor="#0f172a", transform=ax.transAxes))
    ax.add_patch(Rectangle((0, 0.911), 1, 0.004, facecolor="#2563eb", transform=ax.transAxes))
    ax.text(0.04, 0.962, titre_doc, color="white", fontsize=10.2, fontweight="bold", transform=ax.transAxes)
    ax.text(0.04, 0.930, sous_titre, color="#cbd5e1", fontsize=7.4, transform=ax.transAxes)

def _footer_pdf(ax, gauche, page_txt):
    ax.plot([0.04, 0.96], [0.038, 0.038], color="#cbd5e1", lw=0.8, transform=ax.transAxes)
    ax.text(0.04, 0.020, gauche, fontsize=7.0, color="#64748b", transform=ax.transAxes)
    ax.text(0.96, 0.020, page_txt, ha="right", fontsize=7.0, fontweight="bold", color="#475569", transform=ax.transAxes)

def build_pdf_ent_retro(ent, normes, ans_r, p_r, ebe_r, rn_r, bfr_r, tres_r, dette_r, tcma_p, tcma_ebe, ca_sal_r, mb_r, ms_r, atouts_txt, vig_txt):
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69), dpi=150)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        _header_pdf(ax, "ARCHÉTYPE ENTREPRISE  |  REVUE HISTORIQUE & RENTABILITÉ (2021-2025)", f"ENTREPRISE : {ent.upper()}  •  {normes['type_court'].upper()}  •  DYNAMIQUE DE CROISSANCE")

        tx_ebe_fin = (ebe_r[-1]/p_r[-1]*100) if p_r[-1] > 0 else 0
        tx_mb_fin = (mb_r[-1]/p_r[-1]*100) if p_r[-1] > 0 else 0
        levier_fin = (dette_r[-1] - tres_r[-1]) / max(0.01, ebe_r[-1])
        
        kpis = [
            ("TCMA CHIFFRE D'AFFAIRES", f"+{tcma_p:.2f} % / an", "Rythme de croissance global", "#059669" if tcma_p > 0 else "#dc2626"),
            ("MARGE BRUTE (FIN)", f"{tx_mb_fin:.1f} %", f"Évolution EBE : {tcma_ebe:+.2f} %/an", "#2563eb"),
            ("TAUX EBE (FIN)", f"{tx_ebe_fin:.1f} %", f"Cible secteur : {normes['ebe_cible']} %", "#059669" if tx_ebe_fin >= normes["ebe_cible"] else "#d97706"),
            ("LEVIER (DETTE NETTE / EBE)", f"{levier_fin:.1f} x", f"Max bancaire : {normes['levier_dette_max']} x", "#dc2626" if levier_fin > normes["levier_dette_max"] else "#059669"),
        ]
        for i, (kt, kv, ks, kc) in enumerate(kpis):
            kx = 0.04 + i * 0.233
            _box(ax, kx, 0.815, 0.218, 0.084, fc="#f8fafc")
            ax.text(kx + 0.012, 0.880, kt, fontsize=6.7, fontweight="bold", color="#475569", transform=ax.transAxes)
            ax.text(kx + 0.012, 0.845, kv, fontsize=11.8, fontweight="bold", color=kc, transform=ax.transAxes)
            ax.text(kx + 0.012, 0.823, ks, fontsize=6.3, color="#64748b", transform=ax.transAxes)

        ax.text(0.04, 0.785, "1. DYNAMIQUE DE CROISSANCE (CA, MARGE BRUTE & EBE)", fontsize=8.0, fontweight="bold", color="#0f172a", transform=ax.transAxes)
        g1 = fig.add_axes([0.06, 0.595, 0.41, 0.175])
        g1.plot(ans_r, p_r, marker="o", color="#1e293b", lw=2, label="Chiffre d'Affaires")
        g1.plot(ans_r, mb_r, marker="s", color="#2563eb", lw=2, label="Marge Brute")
        g1.plot(ans_r, ebe_r, marker="^", color="#059669", lw=2, label="EBE")
        g1.grid(True, linestyle="--", alpha=0.5)
        g1.tick_params(labelsize=7)
        g1.legend(fontsize=6.8, loc="upper left")

        ax.text(0.52, 0.785, "2. ÉVOLUTION DU BESOIN EN FONDS DE ROULEMENT ET TRÉSORERIE (M€)", fontsize=8.0, fontweight="bold", color="#0f172a", transform=ax.transAxes)
        g2 = fig.add_axes([0.54, 0.595, 0.41, 0.175])
        g2.bar(ans_r, bfr_r, color="#d97706", width=0.4, label="BFR d'Exploitation")
        g2.plot(ans_r, tres_r, marker="s", color="#2563eb", lw=2, label="Trésorerie Nette")
        g2.grid(True, axis="y", linestyle="--", alpha=0.5)
        g2.tick_params(labelsize=7)
        g2.legend(fontsize=6.8, loc="upper left")

        ax.text(0.04, 0.545, "3. TABLEAU DES SOLDES HISTORIQUES (2021-2025)", fontsize=8.0, fontweight="bold", color="#0f172a", transform=ax.transAxes)
        tax = fig.add_axes([0.04, 0.355, 0.92, 0.175])
        tax.axis("off")
        t_rows = [
            ["Chiffre d'Affaires HT (M€)"] + [f"{v:.2f}" for v in p_r],
            ["Marge Brute (M€)"] + [f"{v:.2f}" for v in mb_r],
            [f"Taux d'EBE (Cible {normes['ebe_cible']} %)"] + [f"{(ebe_r[i]/max(0.01,p_r[i])*100):.1f} %" for i in range(len(ans_r))],
            ["Masse Salariale / CA (%)"] + [f"{(ms_r[i]/max(0.01,p_r[i])*100):.1f} %" for i in range(len(ans_r))],
            ["Productivité : CA / Salarié (k€)"] + [f"{v:.0f} k€" for v in ca_sal_r],
            [f"BFR en jours de CA (Norme ~{normes['bfr_jours']} j)"] + [f"{(bfr_r[i]/max(0.01,p_r[i])*365):.0f} j" for i in range(len(ans_r))],
            ["Dette Nette / EBE (x)"] + [f"{((dette_r[i]-tres_r[i])/max(0.01,ebe_r[i])):.1f} x" for i in range(len(ans_r))],
        ]
        tbl = tax.table(cellText=t_rows, colLabels=["Indicateurs d'Exploitation"] + ans_r, loc="center", cellLoc="center", colWidths=[0.44] + [0.112]*len(ans_r))
        tbl.set_fontsize(6.9)
        tbl.scale(1, 1.18)
        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#cbd5e1")
            if r == 0:
                cell.set_facecolor("#1e293b")
                cell.get_text().set_color("white")
                cell.get_text().set_weight("bold")
            elif c == 0:
                cell.get_text().set_ha("left")

        _box(ax, 0.04, 0.060, 0.445, 0.275, fc="#ecfdf5", ec="#6ee7b7")
        ax.text(0.055, 0.305, "ATOUTS & MARGES DE MANŒUVRE", fontsize=7.8, fontweight="bold", color="#065f46", transform=ax.transAxes)
        _draw_lines(ax, 0.055, 0.280, [f"• {x}" for x in atouts_txt], wrap_w=54, step_y=0.019, gap_item=0.008, fontsize=6.8)

        _box(ax, 0.515, 0.060, 0.445, 0.275, fc="#fffbeb", ec="#fcd34d")
        ax.text(0.530, 0.305, "VULNÉRABILITÉS & ALERTES", fontsize=7.8, fontweight="bold", color="#92400e", transform=ax.transAxes)
        _draw_lines(ax, 0.530, 0.280, [f"• {x}" for x in vig_txt], wrap_w=54, step_y=0.019, gap_item=0.008, fontsize=6.8)

        _footer_pdf(ax, "Revue Historique PME  •  Cabinet QUINZE — Direction Financière Externalisée", "Page 1/1")
        pdf.savefig(fig)
        plt.close(fig)
    return buf.getvalue()

def build_pdf_ent_tbi(ent, normes, score, ca, ach_conso, ext_f, va, ms, ebe, fin_val, amo_val, is_val, rn, caf, client, fourn, stock, bfr, dso, dpo, dio, bfr_j, dette, treso, fp, seuil_r, mcv, synth_tbi):
    buf = io.BytesIO()
    fig = plt.figure(figsize=(8.27, 11.69), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    _header_pdf(ax, "ARCHÉTYPE ENTREPRISE  |  FLASH BFR & STRUCTURE FINANCIÈRE", f"ENTREPRISE : {ent.upper()}  •  {normes['type_court'].upper()}  •  CA : {ca:.2f} M€")

    sc_col = "#059669" if score >= 75 else ("#d97706" if score >= 50 else "#dc2626")
    sc_bg = "#ecfdf5" if score >= 75 else ("#fffbeb" if score >= 50 else "#fef2f2")
    _box(ax, 0.04, 0.815, 0.92, 0.084, fc=sc_bg, ec=sc_col, lw=1.4)
    ax.text(0.055, 0.876, "SCORING BDF (Estimé)", fontsize=7.0, fontweight="bold", color="#475569", transform=ax.transAxes)
    ax.text(0.055, 0.832, f"{score}/100", fontsize=17.0, fontweight="bold", color=sc_col, transform=ax.transAxes)
    ax.plot([0.255, 0.255], [0.823, 0.891], color="#cbd5e1", lw=1.0, transform=ax.transAxes)

    statut_msg = "STRUCTURE BILANCIELLE SAINE ET FINANÇABLE" if score >= 75 else ("TENSION SUR LE BFR OU LE LEVIER D'ENDETTEMENT" if score >= 50 else "ALERTE LIQUIDITÉ OU RUPTURE DE COVENANTS BANCAIRES")
    ax.text(0.275, 0.874, statut_msg, fontsize=8.0, fontweight="bold", color=sc_col, transform=ax.transAxes)
    tebe = (ebe / ca * 100) if ca > 0 else 0
    dette_nette = dette - treso
    levier = (dette_nette / ebe) if ebe > 0 else 99.9
    gearing = (dette_nette / fp * 100) if fp > 0 else 999.0
    ax.text(0.275, 0.850, f"EBE : {tebe:.1f}% (Cible {normes['ebe_cible']}%) • Dette Nette / EBE : {levier:.1f} x (Max {normes['levier_dette_max']} x) • Gearing : {gearing:.0f} %", fontsize=6.8, color="#1e293b", transform=ax.transAxes)
    ax.text(0.275, 0.827, f"BFR : {bfr_j:.0f} jours de CA • Délai Clients : {dso:.0f} j (Max {normes['dso_max']} j) • Point Mort : {seuil_r:.2f} M€", fontsize=6.8, color="#1e293b", transform=ax.transAxes)

    _box(ax, 0.04, 0.450, 0.50, 0.350, fc="#ffffff")
    ax.add_patch(Rectangle((0.04, 0.762), 0.50, 0.038, facecolor="#1e293b", transform=ax.transAxes))
    ax.text(0.055, 0.775, "1. CASCADE DES SIG (COMPTABILITÉ COMMERCIALE)", color="white", fontsize=7.9, fontweight="bold", transform=ax.transAxes)
    sig_rows = [
        ("Chiffre d'Affaires HT (70)", f"{ca:.2f} M€", "100%", True, "#1d4ed8"),
        ("- Achats Consommés (60 ± 603)", f"-{ach_conso:.2f} M€", "-", False, "#475569"),
        ("= Marge Brute Globale", f"{(ca - ach_conso):.2f} M€", "-", True, "#0f172a"),
        ("- Services extérieurs & Locaux (61-62)", f"-{ext_f:.2f} M€", "-", False, "#475569"),
        ("= Valeur Ajoutée (VA)", f"{va:.2f} M€", "-", True, "#0f172a"),
        ("- Masse Salariale chargée (64)", f"-{ms:.2f} M€", "-", False, "#475569"),
        ("= EBE (Excédent Brut d'Exploitation)", f"{ebe:.2f} M€", f"{tebe:.1f}%", True, "#059669" if tebe >= normes['ebe_cible'] else "#dc2626"),
        ("- Dotations Amortissements (681)", f"-{amo_val:.2f} M€", "-", False, "#475569"),
        ("- Frais financiers nets (66)", f"-{fin_val:.2f} M€", "-", False, "#475569"),
        ("- Impôt sur les Sociétés (69)", f"-{is_val:.2f} M€", "-", False, "#475569"),
        ("= RÉSULTAT NET COMPTABLE", f"{rn:+.2f} M€", f"{(rn/ca*100):+.1f}%", True, "#059669" if rn >= 0 else "#dc2626"),
        ("= CAPACITÉ D'AUTOFINANCEMENT (CAF)", f"{caf:+.2f} M€", f"{(caf/ca*100):+.1f}%", True, "#0d9488"),
    ]
    ys = 0.735
    for lbl, val, nrm, bld, col in sig_rows:
        if bld: ax.add_patch(Rectangle((0.046, ys - 0.007), 0.488, 0.024, facecolor="#f1f5f9", transform=ax.transAxes))
        ax.text(0.052, ys, lbl, fontsize=6.8, fontweight="bold" if bld else "normal", color="#1e293b", transform=ax.transAxes)
        ax.text(0.420, ys, val, ha="right", fontsize=6.9, fontweight="bold" if bld else "normal", color=col, transform=ax.transAxes)
        ax.text(0.532, ys, nrm, ha="right", fontsize=6.4, color="#64748b", transform=ax.transAxes)
        ys -= 0.027

    _box(ax, 0.56, 0.635, 0.40, 0.165, fc="#ffffff")
    ax.add_patch(Rectangle((0.56, 0.762), 0.40, 0.038, facecolor="#1e293b", transform=ax.transAxes))
    ax.text(0.575, 0.775, "2. LE BESOIN EN FONDS DE ROULEMENT (BFR)", color="white", fontsize=7.9, fontweight="bold", transform=ax.transAxes)
    sc_items = [
        ("Délai Client (DSO) :", f"{dso:.0f} jours (Max {normes['dso_max']} j)"),
        ("Délai Fournisseur (DPO) :", f"{dpo:.0f} jours (Min {normes['dpo_min']} j)"),
        ("Rotation Stocks (DIO) :", f"{dio:.0f} jours (Max {normes['dio_max']} j)"),
        ("BFR d'Exploitation :", f"{bfr:.2f} M€ ({bfr_j:.1f} j. de CA)"),
    ]
    ysc = 0.732
    for k, v in sc_items:
        ax.text(0.572, ysc, k, fontsize=6.7, color="#475569", transform=ax.transAxes)
        ax.text(0.948, ysc, v, ha="right", fontsize=6.8, fontweight="bold", color="#0f172a", transform=ax.transAxes)
        ysc -= 0.028

    _box(ax, 0.56, 0.450, 0.40, 0.170, fc="#ffffff")
    ax.add_patch(Rectangle((0.56, 0.582), 0.40, 0.038, facecolor="#1e293b", transform=ax.transAxes))
    ax.text(0.575, 0.595, "3. ENDETTEMENT & RENTABILITÉ", color="white", fontsize=7.9, fontweight="bold", transform=ax.transAxes)
    det_items = [
        ("Capitaux Propres :", f"{fp:.2f} M€"),
        ("Dette Financière Nette :", f"{dette_nette:.2f} M€"),
        ("Levier (Dette Nette / EBE) :", f"{levier:.1f} x (Max {normes['levier_dette_max']} x)"),
        ("Point Mort (Seuil de rentabilité) :", f"{seuil_r:.2f} M€"),
    ]
    yd = 0.552
    for k, v in det_items:
        ax.text(0.572, yd, k, fontsize=6.7, color="#475569", transform=ax.transAxes)
        ax.text(0.948, yd, v, ha="right", fontsize=6.8, fontweight="bold", color="#0f172a", transform=ax.transAxes)
        yd -= 0.028

    _box(ax, 0.04, 0.275, 0.92, 0.160, fc="#f8fafc", ec="#94a3b8")
    ax.text(0.055, 0.415, "4. SYNTHÈSE EXÉCUTIVE DU DAF & RECOMMANDATIONS", fontsize=8.0, fontweight="bold", color="#0f172a", transform=ax.transAxes)
    _draw_lines(ax, 0.055, 0.390, synth_tbi, wrap_w=124, step_y=0.017, gap_item=0.005, fontsize=7.0)

    ax.text(0.04, 0.248, f"REPÈRES PME SECTORIELS ({normes['type_court'].upper()})", fontsize=7.5, fontweight="bold", color="#0f172a", transform=ax.transAxes)
    refs = [
        ("SEUIL DE RENTABILITÉ", "Couverture des frais fixes", ["Date = 365 * (Seuil / CA)", "Indique le jour de bascule", "S'abaisse si la marge augmente"]),
        ("BFR & TRÉSORERIE", "Cycle d'exploitation", [f"DSO < {normes['dso_max']} j et DPO > {normes['dpo_min']} j", "La croissance consomme du cash", "1 j de DSO gagné = cash immédiat"]),
        ("COVENANTS BANCAIRES", "Capacité d'emprunt", [f"Dette Nette / EBE < {normes['levier_dette_max']} x", "Gearing (Dette/FP) < 100 %", "Conditionne futurs CAPEX ou LBO"]),
        ("EFFET DE CISEAU", "Marges vs Frais", [f"EBE cible >= {normes['ebe_cible']} % du CA", "L'inflation détruit la marge", "Exige une hausse des prix"]),
    ]
    for i, (rt, rf, rbullets) in enumerate(refs):
        rx = 0.04 + i * 0.233
        _box(ax, rx, 0.055, 0.218, 0.180, fc="#ffffff")
        ax.text(rx + 0.012, 0.215, rt, fontsize=6.9, fontweight="bold", color="#0f172a", transform=ax.transAxes)
        ax.text(rx + 0.012, 0.193, rf, fontsize=6.2, color="#64748b", transform=ax.transAxes)
        _draw_lines(ax, rx + 0.012, 0.160, [f"• {b}" for b in rbullets], wrap_w=32, step_y=0.024, gap_item=0.004, fontsize=6.6)

    _footer_pdf(ax, "Flash BFR & TBI certifié  •  Cabinet QUINZE — DAF Externalisé", "Page 1/1")
    fig.savefig(buf, format="pdf")
    plt.close(fig)
    return buf.getvalue()

def build_pdf_ent_prospective(ent, normes, ppi_annuel, df_proj, fcf_30, feuille_route):
    buf = io.BytesIO()
    fig = plt.figure(figsize=(8.27, 11.69), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    _header_pdf(ax, "ARCHÉTYPE ENTREPRISE  |  BUSINESS PLAN & CASH-FLOW (2026-2030)", f"ENTREPRISE : {ent.upper()}  •  {normes['type_court'].upper()}  •  CAPEX : {ppi_annuel:.2f} M€/AN")

    ebe_30 = df_proj["EBE (M€)"].iloc[-1]
    tebe_30 = (ebe_30 / df_proj["Chiffre d'Aff. (M€)"].iloc[-1] * 100)
    dn_ebe_30 = df_proj["Dette Nette / EBE (x)"].iloc[-1]
    tres_30 = df_proj["Trésorerie (M€)"].iloc[-1]

    kpis = [
        ("EBE PROJETÉ 2030", f"{ebe_30:.2f} M€", f"Taux : {tebe_30:.1f}% (Cible {normes['ebe_cible']}%)", "#059669" if tebe_30 >= normes["ebe_cible"] else "#d97706"),
        ("FREE CASH-FLOW 2030", f"{fcf_30:.2f} M€", "Après investissements (CAPEX) & var. BFR", "#059669" if fcf_30 >= 0 else "#dc2626"),
        ("LEVIER DETTE 2030", f"{dn_ebe_30:.1f} x", f"Norme bancaire max : {normes['levier_dette_max']} x", "#059669" if dn_ebe_30 <= normes["levier_dette_max"] else "#dc2626"),
        ("TRÉSORERIE DISPONIBLE", f"{tres_30:.2f} M€", "Sécurité liquidité", "#1d4ed8" if tres_30 >= 0 else "#dc2626"),
    ]
    for i, (kt, kv, ks, kc) in enumerate(kpis):
        kx = 0.04 + i * 0.233
        _box(ax, kx, 0.815, 0.218, 0.084, fc="#f8fafc")
        ax.text(kx + 0.012, 0.880, kt, fontsize=6.9, fontweight="bold", color="#475569", transform=ax.transAxes)
        ax.text(kx + 0.012, 0.845, kv, fontsize=11.8, fontweight="bold", color=kc, transform=ax.transAxes)
        ax.text(kx + 0.012, 0.823, ks, fontsize=6.5, color="#64748b", transform=ax.transAxes)

    ax.text(0.04, 0.785, "1. PROJECTION CA & EXCÉDENT BRUT (M€)", fontsize=7.8, fontweight="bold", color="#0f172a", transform=ax.transAxes)
    gp1 = fig.add_axes([0.06, 0.595, 0.41, 0.175])
    gp1.plot(df_proj["Année"], df_proj["Chiffre d'Aff. (M€)"], marker="o", color="#1e293b", lw=2, label="Chiffre d'Affaires")
    gp1.plot(df_proj["Année"], df_proj["EBE (M€)"], marker="^", color="#059669", lw=2, label="EBE")
    gp1.grid(True, linestyle="--", alpha=0.5)
    gp1.tick_params(labelsize=7)
    gp1.legend(fontsize=6.8, loc="upper left")

    ax.text(0.52, 0.785, "2. TRAJECTOIRE BFR & TRÉSORERIE NETTE (M€)", fontsize=7.8, fontweight="bold", color="#0f172a", transform=ax.transAxes)
    gp2 = fig.add_axes([0.54, 0.595, 0.41, 0.175])
    gp2.bar(df_proj["Année"], df_proj["BFR d'Exploit. (M€)"], color="#d97706", width=0.4, label="BFR d'Exploitation")
    gp2.plot(df_proj["Année"], df_proj["Trésorerie (M€)"], marker="s", color="#2563eb", lw=2, label="Trésorerie Nette")
    gp2.grid(True, axis="y", linestyle="--", alpha=0.5)
    gp2.tick_params(labelsize=7)
    gp2.legend(fontsize=6.8, loc="upper left")

    ax.text(0.04, 0.548, "TABLEAU PLURIANNUEL DES FREE CASH-FLOWS & COVENANTS (2026-2030)", fontsize=8.0, fontweight="bold", color="#0f172a", transform=ax.transAxes)
    tp_ax = fig.add_axes([0.04, 0.340, 0.92, 0.195])
    tp_ax.axis("off")
    t_data = [
        ["Chiffre d'Affaires projeté (M€)"] + [f"{v:.2f}" for v in df_proj["Chiffre d'Aff. (M€)"]],
        [f"EBE (Cible > {normes['ebe_cible']} %)"] + [f"{v:.2f}" for v in df_proj["EBE (M€)"]],
        ["Investissements de croissance & maintien (CAPEX)"] + [f"{ppi_annuel:.2f}"] * 5,
        ["BFR d'Exploitation (M€)"] + [f"{v:.2f}" for v in df_proj["BFR d'Exploit. (M€)"]],
        ["Trésorerie de fin d'exercice (M€)"] + [f"{v:.2f}" for v in df_proj["Trésorerie (M€)"]],
        [f"Levier Bancaire (Dette Nette / EBE < {normes['levier_dette_max']})"] + [f"{v:.1f} x" for v in df_proj["Dette Nette / EBE (x)"]],
    ]
    tbl = tp_ax.table(cellText=t_data, colLabels=["Indicateurs Business Plan"] + list(df_proj["Année"]), loc="center", cellLoc="center", colWidths=[0.46] + [0.108]*5)
    tbl.set_fontsize(6.9)
    tbl.scale(1, 1.18)
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#cbd5e1")
        if r == 0:
            cell.set_facecolor("#1e293b")
            cell.get_text().set_color("white")
            cell.get_text().set_weight("bold")
        elif c == 0:
            cell.get_text().set_ha("left")

    _box(ax, 0.04, 0.055, 0.92, 0.270, fc="#ecfdf5", ec="#6ee7b7")
    ax.text(0.055, 0.305, "MÉMORANDUM DU DAF : EXÉCUTION DU BUSINESS PLAN & LEVIERS", fontsize=7.8, fontweight="bold", color="#065f46", transform=ax.transAxes)
    _draw_lines(ax, 0.055, 0.280, feuille_route, wrap_w=124, step_y=0.016, gap_item=0.005, fontsize=7.0)

    _footer_pdf(ax, "Business Plan 5 ans  •  Cabinet QUINZE — DAF Externalisé", "Page 1/1")
    fig.savefig(buf, format="pdf")
    plt.close(fig)
    return buf.getvalue()

# ==========================================
# 4. INTERFACE DE PARAMÉTRAGE (BARRE LATÉRALE)
# ==========================================
st.sidebar.markdown("## 🏢 Archétype Entreprise")
secteur = st.sidebar.selectbox(
    "Secteur d'activité",
    [
        "Services / Tertiaire",
        "Commerce / Négoce",
        "Industrie / Production",
        "BTP / Construction",
        "Hôtellerie / Restauration"
    ]
)
normes_base = get_normes_pme(secteur)
entreprise = st.sidebar.text_input("Nom de l'entreprise", "TechSolutions SAS")

with st.sidebar.expander("🎯 Normes Sectorielles (Banque de France)", expanded=False):
    st.caption("Ajustez ces repères pour affiner le scoring bancaire.")
    cn1, cn2 = st.columns(2)
    n_ebe = cn1.number_input("Taux EBE cible (%)", value=float(normes_base["ebe_cible"]), step=1.0)
    n_levier = cn2.number_input("Dette/EBE max (x)", value=float(normes_base["levier_dette_max"]), step=0.5)
    cn3, cn4 = st.columns(2)
    n_dso = cn3.number_input("Délai Clients (DSO max)", value=int(normes_base["dso_max"]), step=5)
    n_dpo = cn4.number_input("Délai Fourn. (DPO min)", value=int(normes_base["dpo_min"]), step=5)
    n_dio = st.number_input("Rotation Stocks (DIO max)", value=int(normes_base["dio_max"]), step=5)

normes = dict(normes_base)
normes.update({
    "ebe_cible": n_ebe,
    "levier_dette_max": n_levier,
    "dso_max": n_dso,
    "dpo_min": n_dpo,
    "dio_max": n_dio,
})

c_sb1, c_sb2 = st.sidebar.columns(2)
effectif_etp = c_sb1.number_input("Effectif (ETP)", min_value=1.0, value=25.0, step=1.0)
tx_tva = c_sb2.selectbox("TVA dominante (%)", [20.0, 10.0, 5.5, 0.0], index=0)

st.sidebar.markdown("### 1. Ingestion FEC / Balance (2021-2025)")
fichiers = st.sidebar.file_uploader("Importer balance ou FEC (.csv, .xlsx)", type=["csv", "xlsx", "xls"], accept_multiple_files=True)
balances_parsees = []
if fichiers:
    for f in fichiers:
        rb = parse_balance_pme(f)
        if rb: balances_parsees.append(rb)
balances_parsees.sort(key=lambda x: x["annee"] or x["fichier"])
source_reelle = len(balances_parsees) > 0

ca_base = 2500000.0
mcv_cible = normes["mcv_cible"] / 100.0
if source_reelle:
    dern = balances_parsees[-1]
    d_ca = dern["ca_70"]
    d_subv_expl = dern["subv_74"] + dern["aut_p"]
    d_ach = dern["ach_60"] + dern["var_stock_603"]
    d_ext = dern["ext_61_62"]
    d_ms = dern["ms_64"]
    d_amo = dern["amo_681"]
    d_fin = dern["fin_66"]
    d_is = dern["is_69"]
    d_client = dern["client_41"]
    d_fourn = dern["fourn_40"]
    d_stock = dern["stock_3"]
    d_dette = dern["dette_16"]
    d_remb = dern["remb_16"] if dern["remb_16"] > 0 else 65000.0
    d_tres = dern["tres_51"]
    d_fp = dern["fp_10_12"]
else:
    d_ca = ca_base
    d_subv_expl = 0.0
    d_ach = ca_base * (1.0 - mcv_cible) * 0.8
    d_ext = ca_base * 0.15
    d_ms = ca_base * 0.25
    d_amo = ca_base * 0.04
    d_fin = 15000.0
    d_is = 45000.0
    d_client = d_ca * (1 + tx_tva/100.0) * (normes["dso_max"] / 365.0)
    d_fourn = (d_ach + d_ext) * (1 + tx_tva/100.0) * (normes["dpo_min"] / 365.0)
    d_stock = d_ach * (normes["dio_max"] / 365.0) if normes["dio_max"] > 0 else 0.0
    d_dette = 450000.0
    d_remb = 65000.0
    d_tres = 120000.0
    d_fp = 350000.0

st.sidebar.markdown("### 2. Données d'Exploitation (SIG)")
ca_70 = st.sidebar.number_input("Chiffre d'Affaires HT (70) (€)", value=float(d_ca), step=50000.0)
achats_conso = st.sidebar.number_input("Achats consommés (60 ± 603) (€)", value=float(d_ach), step=10000.0)
ext_61_62 = st.sidebar.number_input("Charges externes (61-62) (€)", value=float(d_ext), step=10000.0)
ms_64 = st.sidebar.number_input("Masse salariale (64) (€)", value=float(d_ms), step=25000.0)
amo_68 = st.sidebar.number_input("Amortissements (681) (€)", value=float(d_amo), step=5000.0)
fin_66 = st.sidebar.number_input("Frais financiers nets (66) (€)", value=float(d_fin), step=1000.0)
is_69 = st.sidebar.number_input("Impôt sur les Sociétés (69) (€)", value=float(d_is), step=5000.0)

st.sidebar.markdown("### 3. Bilan & Besoin en Fonds de Roulement (BFR)")
client_41 = st.sidebar.number_input("Créances Clients TTC (41) (€)", value=float(d_client), step=10000.0)
fourn_40 = st.sidebar.number_input("Dettes Fournisseurs TTC (40) (€)", value=float(d_fourn), step=10000.0)
stock_3 = st.sidebar.number_input("Valeur des Stocks (3) (€)", value=float(d_stock), step=10000.0)
dette_16 = st.sidebar.number_input("Dette Financière Long Terme (16) (€)", value=float(d_dette), step=25000.0)
remb_16 = st.sidebar.number_input("Remboursement annuel capital (€)", value=float(d_remb), step=5000.0)
tres_51 = st.sidebar.number_input("Trésorerie Nette (50/51 - 519) (€)", value=float(d_tres), step=10000.0)
fonds_prop = st.sidebar.number_input("Capitaux Propres (10/12) (€)", value=float(d_fp), step=25000.0)

# ==========================================
# 4. CALCULS DU MOTEUR FINANCIER PME
# ==========================================
marge_brute = ca_70 - achats_conso
tx_marge_brute = (marge_brute / ca_70 * 100) if ca_70 > 0 else 0.0
valeur_ajoutee = marge_brute - ext_61_62
ebe = valeur_ajoutee - ms_64
tx_ebe = (ebe / ca_70 * 100) if ca_70 > 0 else 0.0
rex = ebe - amo_68
rcai = rex - fin_66
rn = rcai - is_69
caf = rn + amo_68
autofin_net = caf - remb_16

bfr = stock_3 + client_41 - fourn_40
ca_ttc = ca_70 * (1 + tx_tva/100.0)
achats_ttc = (achats_conso + ext_61_62) * (1 + tx_tva/100.0)
dso = (client_41 / ca_ttc * 365) if ca_ttc > 0 else 0.0
dpo = (fourn_40 / achats_ttc * 365) if achats_ttc > 0 else 0.0
dio = (stock_3 / max(1.0, achats_conso) * 365) if achats_conso > 0 else 0.0
bfr_jours = (bfr / ca_70 * 365) if ca_70 > 0 else 0.0

dette_nette = dette_16 - tres_51
levier_dette = (dette_nette / ebe) if ebe > 0 else 99.9
gearing = (dette_nette / fonds_prop * 100) if fonds_prop > 0 else 999.0

s_ebe = 30 if tx_ebe >= normes["ebe_cible"] else max(0, int(30 - (normes["ebe_cible"] - tx_ebe) * 3))
s_levier = 30 if levier_dette <= normes["levier_dette_max"] else max(0, int(30 - (levier_dette - normes["levier_dette_max"]) * 10))
s_bfr = 20 if bfr_jours <= normes["bfr_jours"] else max(0, int(20 - (bfr_jours - normes["bfr_jours"])))
s_treso = 20 if tres_51 > 0 else 0
health_score = min(100, s_ebe + s_levier + s_bfr + s_treso)

# ==========================================
# 5. VUE PRINCIPALE & ONGLETS
# ==========================================
st.title("Archétype Entreprise — DAF Externalisé & Pilotage BFR")
badge_src = "Balances commerciales vérifiées" if source_reelle else "Modèle calibré"
st.caption(f"Entreprise : **{entreprise}** • {normes['type_court']} • CA : {ca_70/1e6:.2f} M€ • {effectif_etp:.1f} ETP • {badge_src}")

with st.expander("📖 Guide du Dirigeant : Trésorerie, Rentabilité & Covenants Bancaires", expanded=False):
    st.markdown("Ce guide simplifie la comptabilité classique pour se concentrer sur les flux de trésorerie (Cash is King) :")
    gp1, gp2, gp3 = st.columns(3)
    with gp1:
        st.markdown("**1. Le Point Mort (Seuil de Rentabilité)**")
        st.markdown("""* **Principe :** Il faut payer les frais fixes (salaires, loyers, assurances) chaque mois, même sans CA. Le seuil de rentabilité est le chiffre d'affaires exact à atteindre pour que la marge sur les ventes couvre 100 % de ces frais fixes.
* **Le levier :** Une fois ce point mort dépassé, chaque euro vendu supplémentaire va presque intégralement se transformer en bénéfice net.""")
    with gp2:
        st.markdown("**2. Le BFR (Besoin en Fonds de Roulement)**")
        st.markdown("""* **Le piège de la croissance :** Quand le CA augmente, le stock et les créances clients (argent dehors) augmentent. L'entreprise doit financer ce décalage avant d'être payée. C'est le BFR.
* **Cash immédiat :** Réduire de 5 jours le délai de paiement des clients (DSO) libère instantanément plusieurs milliers d'euros sur le compte bancaire.""")
    with gp3:
        st.markdown("**3. Covenants et Dette Nette / EBE**")
        st.markdown(f"""* **Capacité d'emprunt :** Le banquier ne regarde pas que le résultat net, il regarde l'EBE (la vraie performance opérationnelle).
* **Levier :** `Dette Nette / EBE` (ici Max bancaire : **{normes['levier_dette_max']} x**). Si ce ratio est dépassé, la banque refusera un nouveau prêt pour des machines ou un rachat (LBO).""")

onglets = st.tabs([
    "📊 1. Rétrospective (Croissance & Marges)",
    "⚖️ 2. Situation à date (SIG & Endettement)",
    "🔪 3. Scalpel (Simulateur BFR & Rentabilité)",
    "🔮 4. Prospective (Free Cash-Flow & LBO)"
])

# ------------------------------------------
# ONGLET 1 : RÉTROSPECTIVE
# ------------------------------------------
with onglets[0]:
    st.subheader("D'où venons-nous ? Dynamique de croissance, Effet ciseau et Marges")
    
    if len(balances_parsees) >= 2:
        ans_r = [b["annee"] or f"Ex.{i+1}" for i, b in enumerate(balances_parsees)]
        p_r = [b["ca_70"]/1e6 for b in balances_parsees]
        mb_r = [(b["ca_70"] - b["ach_60"] - b["var_stock_603"])/1e6 for b in balances_parsees]
        ebe_r = [(b["ca_70"] - b["ach_60"] - b["var_stock_603"] - b["ext_61_62"] - b["ms_64"])/1e6 for b in balances_parsees]
        rn_r = [b["rn"]/1e6 for b in balances_parsees]
        bfr_r = [(b["stock_3"] + b["client_41"] - b["fourn_40"] - b["etat_44"])/1e6 for b in balances_parsees]
        tres_r = [b["tres_51"]/1e6 for b in balances_parsees]
        dette_r = [b["dette_16"]/1e6 for b in balances_parsees]
        ms_r = [b["ms_64"]/1e6 for b in balances_parsees]
        ca_sal_r = [(b["ca_70"]/max(1.0, effectif_etp))/1e3 for b in balances_parsees]
    else:
        ans_r = ["2021", "2022", "2023", "2024", "2025"]
        ca_m, mb_m, ebe_m, rn_m, bfr_m, tr_m, det_m = ca_70/1e6, marge_brute/1e6, ebe/1e6, rn/1e6, bfr/1e6, tres_51/1e6, dette_16/1e6
        p_r = [round(ca_m*0.80, 2), round(ca_m*0.86, 2), round(ca_m*0.92, 2), round(ca_m*0.96, 2), round(ca_m, 2)]
        mb_r = [round(mb_m*0.78, 2), round(mb_m*0.85, 2), round(mb_m*0.91, 2), round(mb_m*0.95, 2), round(mb_m, 2)]
        ebe_r = [round(ebe_m*0.70, 2), round(ebe_m*0.82, 2), round(ebe_m*0.88, 2), round(ebe_m*0.93, 2), round(ebe_m, 2)]
        rn_r = [round(rn_m*0.65, 2), round(rn_m*0.80, 2), round(rn_m*0.85, 2), round(rn_m*0.90, 2), round(rn_m, 2)]
        bfr_r = [round(bfr_m*0.75, 2), round(bfr_m*0.82, 2), round(bfr_m*0.90, 2), round(bfr_m*0.95, 2), round(bfr_m, 2)]
        tres_r = [round(tr_m*1.4, 2), round(tr_m*1.2, 2), round(tr_m*1.1, 2), round(tr_m*1.05, 2), round(tr_m, 2)]
        dette_r = [round(det_m*1.6, 2), round(det_m*1.4, 2), round(det_m*1.2, 2), round(det_m*1.1, 2), round(det_m, 2)]
        ms_r = [round((ms_64/1e6)*0.85, 2), round((ms_64/1e6)*0.90, 2), round((ms_64/1e6)*0.94, 2), round((ms_64/1e6)*0.97, 2), round(ms_64/1e6, 2)]
        ca_sal_r = [round((p * 1e6) / max(1.0, effectif_etp * 0.9) / 1e3, 0) for p in p_r[:-1]] + [round((ca_70 / effectif_etp) / 1e3, 0)]

    n_int = max(1, len(ans_r) - 1)
    tcma_p = ((p_r[-1]/p_r[0])**(1/n_int) - 1)*100 if p_r[0] > 0 else 0.0
    tcma_ebe = ((ebe_r[-1]/ebe_r[0])**(1/n_int) - 1)*100 if ebe_r[0] > 0 else 0.0

    kr1, kr2, kr3, kr4 = st.columns(4)
    kr1.metric("Croissance CA (TCMA)", f"+{tcma_p:.2f} % / an", "Rythme de développement")
    kr2.metric("Évolution EBE", f"+{tcma_ebe:.2f} % / an", f"Marge EBE fin : {(ebe_r[-1]/p_r[-1]*100):.1f} %", delta_color="normal" if tcma_ebe >= tcma_p else "inverse")
    kr3.metric("Productivité RH", f"{ca_sal_r[-1]:.0f} k€ / ETP", f"Évolution : {(ca_sal_r[-1]-ca_sal_r[0]):+.0f} k€", delta_color="normal" if ca_sal_r[-1] > ca_sal_r[0] else "inverse")
    kr4.metric("Levier Financier", f"{((dette_r[-1]-tres_r[-1])/max(0.01, ebe_r[-1])):.1f} x", f"Dette Nette / EBE (Max {normes['levier_dette_max']} x)", delta_color="normal" if ((dette_r[-1]-tres_r[-1])/max(0.01, ebe_r[-1])) <= normes["levier_dette_max"] else "inverse")

    cg1, cg2 = st.columns(2)
    with cg1:
        st.markdown("**1. Effet Ciseau & Marges (M€)**")
        st.line_chart(pd.DataFrame({"Exercice": ans_r, "Chiffre d'Affaires": p_r, "Marge Brute": mb_r, "EBE": ebe_r}).set_index("Exercice"))
    with cg2:
        st.markdown("**2. BFR & Trésorerie (M€)**")
        df_bfr = pd.DataFrame({
            "Exercice": ans_r, 
            "BFR d'Exploitation": bfr_r, 
            "Trésorerie Nette": tres_r
        }).set_index("Exercice")
        st.bar_chart(df_bfr, color=["#d97706", "#2563eb"])

    df_retro_tbl = pd.DataFrame({
        "Indicateurs d'Exploitation & Rentabilité": [
            "Chiffre d'Affaires HT (M€)", "Marge Brute (M€)", f"Taux d'EBE (Cible {normes['ebe_cible']} %)",
            "Masse Salariale / CA (%)", "Productivité : CA / Salarié (k€)", 
            f"BFR en jours de CA (Norme ~{normes['bfr_jours']} j)", "Dette Nette / EBE (x)"
        ],
        **{ans_r[i]: [
            f"{p_r[i]:.2f}", f"{mb_r[i]:.2f}", f"{(ebe_r[i]/max(0.01,p_r[i])*100):.1f} %", 
            f"{(ms_r[i]/max(0.01,p_r[i])*100):.1f} %", f"{ca_sal_r[i]:.0f} k€", 
            f"{(bfr_r[i]/max(0.01,p_r[i])*365):.0f} j", f"{((dette_r[i]-tres_r[i])/max(0.01,ebe_r[i])):.1f} x"
        ] for i in range(len(ans_r))}
    })
    st.dataframe(df_retro_tbl, hide_index=True, use_container_width=True)

    atouts_txt = [
        f"Croissance globale du chiffre d'affaires de +{tcma_p:.2f} %/an, avec un EBE qui suit la dynamique (+{tcma_ebe:.2f} %/an).",
        f"Productivité par collaborateur en {'hausse' if ca_sal_r[-1] >= ca_sal_r[0] else 'baisse'} pour atteindre {ca_sal_r[-1]:.0f} k€/ETP.",
        f"Désendettement structurel portant le levier bancaire à {((dette_r[-1]-tres_r[-1])/max(0.01, ebe_r[-1])):.1f} x (favorable pour des LBO ou CAPEX)."
    ]
    vig_txt = [
        f"Le BFR d'exploitation consomme {(bfr_r[-1]/max(0.01,p_r[-1])*365):.0f} jours de CA (la norme métier est de {normes['bfr_jours']} jours).",
        f"Taux d'EBE de {(ebe_r[-1]/p_r[-1]*100):.1f} % par rapport à une cible sectorielle de {normes['ebe_cible']} %.",
        "Sécuriser la marge brute face à l'inflation des achats pour éviter un effet de ciseau commercial."
    ]

    pdf_retro_bytes = build_pdf_ent_retro(entreprise, normes, ans_r, p_r, ebe_r, rn_r, bfr_r, tres_r, dette_r, tcma_p, tcma_ebe, ca_sal_r, mb_r, ms_r, atouts_txt, vig_txt)
    st.download_button("📑 Télécharger la Revue Historique & Rentabilité (PDF 1 page)", data=pdf_retro_bytes, file_name=f"Revue_Historique_{entreprise.replace(' ','_')}.pdf", mime="application/pdf", type="primary")

    afficher_synthese_unique(
        "Revue Historique (2021-2025)",
        f"Sur la période, l'entreprise affiche une croissance de +{tcma_p:.2f} %/an, transformant un CA de {p_r[-1]:.2f} M€ en {ebe_r[-1]:.2f} M€ d'EBE (soit une marge opérationnelle de {(ebe_r[-1]/p_r[-1]*100):.1f} %).",
        f"Le levier financier (Dette Nette / EBE) se situe à {((dette_r[-1]-tres_r[-1])/max(0.01, ebe_r[-1])):.1f} x, laissant de la marge de manœuvre bancaire (covenant à {normes['levier_dette_max']} x) pour investir.",
        f"Le pilotage du BFR ({(bfr_r[-1]/max(0.01,p_r[-1])*365):.0f} jours de CA) doit être une priorité absolue avant de lancer une nouvelle phase de croissance pour éviter les tensions de trésorerie."
    )

# ------------------------------------------
# ONGLET 2 : SITUATION À DATE (SIG & COVENANTS)
# ------------------------------------------
with onglets[1]:
    st.subheader("Où en est la structure ? Flash BFR, Cascade des SIG & Covenants Bancaires")
    
    hs1, hs2 = st.columns([1, 2.5])
    with hs1:
        st.metric("SCORING BDF (Estimé)", f"{health_score} / 100", "Solide >= 75 | Vigilance 50-74")
    with hs2:
        if health_score >= 75:
            st.success(f"**Structure bilancielle solide ({health_score}/100) :** L'EBE de {tx_ebe:.1f} % dégage une CAF suffisante. Le BFR est maîtrisé ({bfr_jours:.0f} jours) et le levier d'endettement ({levier_dette:.1f} x) est largement dans les clous bancaires.")
        elif health_score >= 50:
            st.warning(f"**Tension bilancielle ({health_score}/100) :** L'EBE ({tx_ebe:.1f} % vs cible {normes['ebe_cible']} %) ou le poids du BFR ({bfr_jours:.0f} jours de CA) fragilise la structure. Risque sur l'autofinancement des investissements.")
        else:
            st.error(f"**Alerte liquidité & covenants ({health_score}/100) :** Risque critique. Levier de dette excessif ({levier_dette:.1f} x) ou BFR hors de contrôle, menaçant la solvabilité à court terme.")

    st.markdown("**1. Cascade des Soldes Intermédiaires de Gestion (Comptabilité Commerciale)**")
    df_sig = pd.DataFrame([
        {"SIG": "Chiffre d'Affaires HT (70)", "Montant (€)": f"{ca_70:,.0f} €".replace(",", " "), "% CA": "100,0 %", "Norme Sectorielle": "Base 100 %"},
        {"SIG": "- Achats Consommés (60 ± 603)", "Montant (€)": f"-{achats_conso:,.0f} €".replace(",", " "), "% CA": f"{achats_conso/ca_70*100:.1f} %", "Norme Sectorielle": "-"},
        {"SIG": "= MARGE BRUTE GLOBALE", "Montant (€)": f"{marge_brute:,.0f} €".replace(",", " "), "% CA": f"{tx_marge_brute:.1f} %", "Norme Sectorielle": "-"},
        {"SIG": "- Services extérieurs & Locaux (61-62)", "Montant (€)": f"-{ext_61_62:,.0f} €".replace(",", " "), "% CA": f"{ext_61_62/ca_70*100:.1f} %", "Norme Sectorielle": "-"},
        {"SIG": "= VALEUR AJOUTÉE COMMERCIALE (VA)", "Montant (€)": f"{valeur_ajoutee:,.0f} €".replace(",", " "), "% CA": f"{valeur_ajoutee/ca_70*100:.1f} %", "Norme Sectorielle": "-"},
        {"SIG": "- Masse Salariale chargée (64)", "Montant (€)": f"-{ms_64:,.0f} €".replace(",", " "), "% CA": f"{ms_64/ca_70*100:.1f} %", "Norme Sectorielle": "-"},
        {"SIG": "= EXCÉDENT BRUT D'EXPLOITATION (EBE)", "Montant (€)": f"{ebe:,.0f} €".replace(",", " "), "% CA": f"{tx_ebe:.1f} %", "Norme Sectorielle": f"Cible : {normes['ebe_cible']} %"},
        {"SIG": "- Dotations Amortissements (681)", "Montant (€)": f"-{amo_68:,.0f} €".replace(",", " "), "% CA": f"{amo_68/ca_70*100:.1f} %", "Norme Sectorielle": "-"},
        {"SIG": "- Frais financiers nets (66)", "Montant (€)": f"-{fin_66:,.0f} €".replace(",", " "), "% CA": f"{fin_66/ca_70*100:.1f} %", "Norme Sectorielle": "Norme < 2,0 %"},
        {"SIG": "- Impôt sur les Sociétés (IS - 69)", "Montant (€)": f"-{is_69:,.0f} €".replace(",", " "), "% CA": f"{is_69/ca_70*100:.1f} %", "Norme Sectorielle": "-"},
        {"SIG": "= RÉSULTAT NET COMPTABLE (Bénéfice)", "Montant (€)": f"{rn:+,.0f} €".replace(",", " "), "% CA": f"{rn/ca_70*100:+.1f} %", "Norme Sectorielle": "Norme >= 0 %"},
        {"SIG": "= CAPACITÉ D'AUTOFINANCEMENT (CAF)", "Montant (€)": f"{caf:+,.0f} €".replace(",", " "), "% CA": f"{caf/ca_70*100:+.1f} %", "Norme Sectorielle": "Permet de rembourser la dette"},
    ])
    st.dataframe(
        df_sig, hide_index=True, use_container_width=True,
        column_config={
            "SIG": st.column_config.TextColumn("Solde Intermédiaire de Gestion", width="large"),
            "Montant (€)": st.column_config.TextColumn("Montant (€)", width="small"),
            "% CA": st.column_config.TextColumn("% CA", width="small"),
            "Norme Sectorielle": st.column_config.TextColumn("Norme / Repère", width="medium"),
        }
    )

    st.markdown("---")
    c_b1, c_b2 = st.columns(2)
    with c_b1:
        st.markdown("**2. Focus : Besoin en Fonds de Roulement (BFR)**")
        b1, b2 = st.columns(2)
        b1.metric("Délai Client Moyen (DSO)", f"{dso:.0f} jours", f"Max secteur : {normes['dso_max']} j", delta_color="inverse" if dso > normes["dso_max"] else "normal")
        b2.metric("Délai Fournisseur (DPO)", f"{dpo:.0f} jours", f"Min secteur : {normes['dpo_min']} j", delta_color="normal" if dpo >= normes["dpo_min"] else "inverse")
        b3, b4 = st.columns(2)
        b3.metric("Rotation Stocks (DIO)", f"{dio:.0f} jours", f"Max secteur : {normes['dio_max']} j", delta_color="inverse" if dio > normes["dio_max"] else "normal")
        b4.metric("BFR d'Exploitation", f"{bfr_jours:.1f} jours", f"Cash bloqué : {bfr:,.0f} €".replace(",", " "), delta_color="inverse" if bfr_jours > normes["bfr_jours"] else "normal")

    with c_b2:
        st.markdown("**3. Covenants Bancaires & Levier d'Endettement**")
        b5, b6 = st.columns(2)
        b5.metric("Dette Nette / EBE (Levier)", f"{levier_dette:.1f} x", f"Plafond LBO/Crédit : {normes['levier_dette_max']} x", delta_color="inverse" if levier_dette > normes["levier_dette_max"] else "normal")
        b6.metric("Gearing (Dette / FP)", f"{gearing:.0f} %", "Norme bancaire < 100 %", delta_color="inverse" if gearing > 100 else "normal")
        b7, b8 = st.columns(2)
        b7.metric("Trésorerie Nette", f"{tres_51:,.0f} €".replace(",", " "), "Coussin de liquidité")
        b8.metric("Autofinancement Net", f"{autofin_net:+,.0f} €".replace(",", " "), "CAF - Remboursement capital", delta_color="normal" if autofin_net >= 0 else "inverse")

    charges_variables_est = achats_conso + (ext_61_62 * 0.4)
    charges_fixes_est = ms_64 + amo_68 + (ext_61_62 * 0.6) + fin_66
    mcv_est = ca_70 - charges_variables_est
    tx_mcv_est = (mcv_est / ca_70) if ca_70 > 0 else 0.0
    seuil_rentabilite_est = (charges_fixes_est / tx_mcv_est) if tx_mcv_est > 0 else 0.0

    synth_tbi = [
        f"• Rentabilité d'exploitation : Taux d'EBE à {tx_ebe:.1f} % (cible {normes['ebe_cible']} %) générant une CAF de {caf/1e6:.2f} M€ après impôts.",
        f"• Structure financière : Levier d'endettement net très sain à {levier_dette:.1f} x (covenant bancaire max {normes['levier_dette_max']} x) autorisant de futures capacités d'emprunt (LBO, CAPEX).",
        f"• Cycle BFR : L'exploitation bloque l'équivalent de {bfr_jours:.1f} jours de chiffre d'affaires en cash ({bfr/1e6:.2f} M€), avec un délai client de {dso:.0f} jours."
    ]
    pdf_tbi_bytes = build_pdf_ent_tbi(entreprise, normes, health_score, ca_70/1e6, achats_conso/1e6, ext_61_62/1e6, valeur_ajoutee/1e6, ms_64/1e6, ebe/1e6, fin_66/1e6, amo_68/1e6, is_69/1e6, rn/1e6, caf/1e6, client_41/1e6, fourn_40/1e6, stock_3/1e6, bfr/1e6, dso, dpo, dio, bfr_jours, dette_16/1e6, tres_51/1e6, fonds_prop/1e6, seuil_rentabilite_est/1e6, mcv_est/1e6, synth_tbi)
    st.download_button("📑 Télécharger le Flash BFR & TBI (PDF 1 page)", data=pdf_tbi_bytes, file_name=f"TBI_BFR_{entreprise.replace(' ','_')}.pdf", mime="application/pdf", type="primary")

    afficher_synthese_unique(
        "Situation à date & Capacité d'endettement",
        f"L'entreprise dégage une marge d'EBE de {tx_ebe:.1f} % ({ebe/1e6:.2f} M€) et une CAF de {caf/1e6:.2f} M€ lui permettant d'afficher un levier d'endettement (Dette Nette / EBE) de {levier_dette:.1f} x, très en deçà du plafond bancaire de {normes['levier_dette_max']} x.",
        f"La trésorerie nette de {tres_51:,.0f} € et l'autofinancement net positif ({autofin_net:+,.0f} € après remboursement du capital) sécurisent la liquidité.".replace(",", " "),
        f"Sécuriser le délai d'encaissement client (actuellement {dso:.0f} jours). Avec ce levier bancaire sain, l'entreprise est finançable pour des opérations de croissance externe (LBO)."
    )

# ------------------------------------------
# ONGLET 3 : SCALPEL (LE BAC À SABLE DU DG)
# ------------------------------------------
with onglets[2]:
    st.subheader("Le Simulateur PME : Optimisation Trésorerie & Point Mort en temps réel")
    
    afficher_aide_pedagogique_onglet("Pilotage de la trésorerie et rentabilité au quotidien", [
        ("Le BFR (Besoin en Fonds de Roulement)", "C'est l'argent immobilisé par l'exploitation. Si le BFR augmente plus vite que le CA, l'entreprise peut faire faillite en pleine croissance par manque de cash."),
        ("DSO (Days Sales Outstanding)", "Délai moyen de paiement des clients. Chaque jour gagné libère de la trésorerie immédiate."),
        ("Seuil de Rentabilité (Point Mort)", "Le chiffre d'affaires minimum à réaliser pour couvrir tous les frais fixes (salaires, loyers, amortissements). En deçà, l'entreprise perd de l'argent.")
    ])

    st.markdown("#### 1. Simulateur BFR : Cash libéré par l'optimisation des délais")
    st.caption("Ajustez les curseurs pour simuler l'impact d'une meilleure politique de recouvrement ou d'achat sur le compte en banque.")
    
    sim1, sim2 = st.columns(2)
    var_dso = sim1.slider("Réduction du délai de paiement clients (DSO - en jours gagnés)", 0, int(max(10, dso)), 0)
    var_dpo = sim2.slider("Allongement du délai de règlement fournisseurs (DPO - en jours gagnés)", 0, 45, 0)
    
    cash_gagne_clients = (var_dso / 365.0) * ca_ttc
    cash_gagne_fourn = (var_dpo / 365.0) * achats_ttc
    cash_total_libere = cash_gagne_clients + cash_gagne_fourn
    nouveau_bfr = bfr - cash_total_libere
    
    c_sb1, c_sb2, c_sb3 = st.columns(3)
    c_sb1.metric("Cash libéré sur les Clients", f"+ {cash_gagne_clients:,.0f} €".replace(",", " "))
    c_sb2.metric("Cash libéré sur les Fournisseurs", f"+ {cash_gagne_fourn:,.0f} €".replace(",", " "))
    c_sb3.metric("NOUVELLE TRÉSORERIE NETTE", f"{(tres_51 + cash_total_libere):,.0f} €".replace(",", " "), f"BFR abaissé à {(nouveau_bfr/max(1,ca_70)*365):.0f} jours de CA")
    
    if cash_total_libere > 0:
        st.success(f"💡 **Action DAF :** Mettre en place une procédure de relance client à J+5 et renégocier les conditions de paiement fournisseurs libérerait **{cash_total_libere:,.0f} €** de cash sans devoir faire un seul euro de vente supplémentaire.".replace(",", " "))

    st.divider()

    st.markdown("#### 2. Simulateur Point Mort : Décisions d'Investissement & RH")
    st.caption("Ajustez vos charges pour voir à quelle date dans l'année l'entreprise devient rentable.")
    
    cv_base = achats_conso + (ext_61_62 * 0.4)
    cf_base = ms_64 + amo_68 + (ext_61_62 * 0.6) + fin_66
    
    sim3, sim4, sim5 = st.columns(3)
    ajout_embauche = sim3.number_input("Nouvelles embauches (Salaires + Charges €/an)", value=0, step=10000)
    ajout_loyer_equip = sim4.number_input("Nouveaux loyers / équipements fixes (€/an)", value=0, step=5000)
    hausse_prix_vente = sim5.slider("Augmentation moyenne des prix de vente (%)", 0.0, 15.0, 0.0, 0.5)

    ca_sim = ca_70 * (1 + hausse_prix_vente/100.0)
    cf_sim = cf_base + ajout_embauche + ajout_loyer_equip
    cv_sim = cv_base
    
    mcv_sim = ca_sim - cv_sim
    tx_mcv_sim = (mcv_sim / ca_sim) if ca_sim > 0 else 0.0
    seuil_rentabilite_sim = (cf_sim / tx_mcv_sim) if tx_mcv_sim > 0 else 0.0
    point_mort_jours_sim = (seuil_rentabilite_sim / ca_sim * 365) if ca_sim > 0 else 365
    
    import datetime
    try:
        date_pm = (datetime.date(2025, 1, 1) + datetime.timedelta(days=int(point_mort_jours_sim)-1)).strftime("%d %B")
    except:
        date_pm = "Jamais (Perte)"

    c_sr1, c_sr2, c_sr3 = st.columns(3)
    c_sr1.metric("Marge sur Coûts Variables (MCV)", f"{tx_mcv_sim*100:.1f} % du CA", f"Base actuelle : {tx_mcv_est*100:.1f} %")
    c_sr2.metric("Frais Fixes (Structure)", f"{cf_sim:,.0f} €".replace(",", " "), f"Base actuelle : {cf_base:,.0f} €".replace(",", " "), delta_color="inverse" if cf_sim > cf_base else "normal")
    c_sr3.metric("NOUVEAU POINT MORT", f"{seuil_rentabilite_sim:,.0f} €".replace(",", " "), f"Atteint le {date_pm} (Jour {int(point_mort_jours_sim)})", delta_color="inverse" if seuil_rentabilite_sim > seuil_rentabilite_est else "normal")

    afficher_synthese_unique(
        "Simulateur de Décision (BFR & Point Mort)",
        f"L'entreprise immobilise aujourd'hui {bfr_jours:.1f} jours de CA en BFR (délai client de {dso:.0f} j). Son point mort structurel de base est à {seuil_rentabilite_est/1e6:.2f} M€ de chiffre d'affaires.",
        f"La marge sur coûts variables de {tx_mcv_est*100:.1f} % permet un fort effet de levier sur la rentabilité pour toute croissance au-delà du seuil de rentabilité.",
        "Utilisez le simulateur ci-dessus en réunion DG pour conditionner vos prochaines embauches ou l'achat d'équipements à un gain d'efficacité sur le recouvrement client ou à une hausse tarifaire ciblée."
    )

# ------------------------------------------
# ONGLET 4 : PROSPECTIVE (BUSINESS PLAN À 5 ANS)
# ------------------------------------------
with onglets[3]:
    st.subheader("Où allons-nous ? Business Plan à 5 ans, Free Cash-Flow et LBO")
    afficher_aide_pedagogique_onglet("Soutenabilité de la croissance & Création de valeur", [
        ("Le Free Cash-Flow (FCF)", "C'est le flux de trésorerie disponible. C'est l'argent qu'il reste VRAIMENT dans l'entreprise à la fin de l'année après avoir payé l'exploitation, l'impôt, la variation du BFR et les investissements de renouvellement. C'est ce FCF qui permet de verser des dividendes ou de rembourser la dette d'acquisition dans un LBO."),
        ("CAPEX (Capital Expenditure)", "Dépenses d'investissement (machines, IT, locaux). Tout CAPEX est une sortie de trésorerie l'année N, qui générera des amortissements (charges fixes) sur les années suivantes."),
        ("Levier Bancaire LBO", "Dans le cadre d'un rachat, la banque vérifiera que le ratio Dette Nette / EBE de la cible reste inférieur à 3 ou 3,5 fois pour garantir que la cible génère assez de cash pour rembourser sa dette.")
    ])

    sp1, sp2, sp3, sp4 = st.columns(4)
    with sp1:
        st.markdown("**1. Hypothèses de Croissance**")
        tx_ca_vol = st.slider("Croissance CA en volumes (%/an)", -5.0, 15.0, 4.0, 0.5)
        tx_ca_prix = st.slider("Croissance CA en prix (%/an)", 0.0, 10.0, 2.0, 0.5)
    with sp2:
        st.markdown("**2. Inflation des Coûts**")
        tx_ach_proj = st.slider("Inflation achats matières/march. (%/an)", 0.0, 10.0, 2.5, 0.5)
        tx_ms_proj = st.slider("Évolution masse salariale brute (%/an)", 0.0, 10.0, 3.5, 0.5)
    with sp3:
        st.markdown("**3. CAPEX (Investissements)**")
        ppi_annuel = st.slider("Enveloppe CAPEX annuel (M€/an)", 0.0, 5.0, 0.15, 0.05)
        duree_amo_capex = st.selectbox("Durée amortissement CAPEX", [3, 5, 7, 10], index=1)
    with sp4:
        st.markdown("**4. Financement & BFR**")
        emprunt_capex_pct = st.slider("Financement bancaire CAPEX (%)", 0, 100, 70, 5)
        bfr_j_cible = st.slider("BFR d'exploitation ciblé (jours CA)", 0, 120, int(bfr_jours), 5)

    ans_p = ["2026", "2027", "2028", "2029", "2030"]
    emprunt_annuel = ppi_annuel * (emprunt_capex_pct / 100.0)
    
    p_list, ebe_list, is_list, caf_list, bfr_list, fcf_list, det_list, tr_list, levier_list = [], [], [], [], [], [], [], [], []
    
    curr_ca = ca_70 / 1e6
    curr_ach = achats_conso / 1e6
    curr_ext = ext_61_62 / 1e6
    curr_ms = ms_64 / 1e6
    curr_amo = amo_68 / 1e6
    curr_fin = fin_66 / 1e6
    curr_det = dette_16 / 1e6
    curr_remb = remb_16 / 1e6
    curr_treso = tres_51 / 1e6
    curr_bfr = bfr / 1e6
    taux_is = 0.25

    for _ in ans_p:
        # P&L Projection
        curr_ca *= (1 + tx_ca_vol/100.0) * (1 + tx_ca_prix/100.0)
        curr_ach *= (1 + tx_ca_vol/100.0) * (1 + tx_ach_proj/100.0) 
        curr_ext *= (1 + tx_ca_vol/100.0 * 0.5) * (1 + tx_ach_proj/100.0) 
        curr_ms *= (1 + tx_ms_proj/100.0)
        curr_amo += ppi_annuel / duree_amo_capex
        curr_fin += emprunt_annuel * 0.045 
        
        curr_ebe = curr_ca - curr_ach - curr_ext - curr_ms
        curr_rex = curr_ebe - curr_amo
        curr_rcai = curr_rex - curr_fin
        curr_is = curr_rcai * taux_is if curr_rcai > 0 else 0.0
        curr_rn = curr_rcai - curr_is
        curr_caf = curr_rn + curr_amo
        
        nouveau_bfr = (curr_ca / 365.0) * bfr_j_cible
        var_bfr = nouveau_bfr - curr_bfr
        curr_bfr = nouveau_bfr
        
        curr_fcf = curr_caf - var_bfr - ppi_annuel
        
        curr_det = max(0.0, curr_det - curr_remb + emprunt_annuel)
        curr_treso = curr_treso + curr_fcf + emprunt_annuel - curr_remb
        
        curr_levier = (curr_det - curr_treso) / max(0.01, curr_ebe)

        p_list.append(round(curr_ca, 2))
        ebe_list.append(round(curr_ebe, 2))
        is_list.append(round(curr_is, 2))
        caf_list.append(round(curr_caf, 2))
        bfr_list.append(round(curr_bfr, 2))
        fcf_list.append(round(curr_fcf, 2))
        det_list.append(round(curr_det, 2))
        tr_list.append(round(curr_treso, 2))
        levier_list.append(round(curr_levier, 1))

    df_proj = pd.DataFrame({
        "Année": ans_p,
        "Chiffre d'Aff. (M€)": p_list,
        "EBE (M€)": ebe_list,
        "IS (M€)": is_list,
        "CAF (M€)": caf_list,
        "BFR d'Exploit. (M€)": bfr_list,
        "Free Cash-Flow (M€)": fcf_list,
        "Dette Long Terme (M€)": det_list,
        "Trésorerie (M€)": tr_list,
        "Dette Nette / EBE (x)": levier_list
    })

    kp1, kp2, kp3, kp4 = st.columns(4)
    tcaf_30 = (caf_list[-1]/p_list[-1]*100)
    kp1.metric("EBE Projeté 2030", f"{ebe_list[-1]:.2f} M€", f"Marge : {(ebe_list[-1]/p_list[-1]*100):.1f} % (Cible {normes['ebe_cible']} %)", delta_color="normal" if (ebe_list[-1]/p_list[-1]*100) >= normes["ebe_cible"] else "inverse")
    kp2.metric("Free Cash-Flow Cumulé (5 ans)", f"{sum(fcf_list):.2f} M€", "Après impôts, CAPEX & var BFR", delta_color="normal" if sum(fcf_list) >= 0 else "inverse")
    kp3.metric("Levier Bancaire 2030", f"{levier_list[-1]:.1f} x", f"Dette Nette / EBE (Max {normes['levier_dette_max']} x)", delta_color="normal" if levier_list[-1] <= normes["levier_dette_max"] else "inverse")
    kp4.metric("Trésorerie Projetée 2030", f"{tr_list[-1]:.2f} M€", f"Variation : {(tr_list[-1] - tres_51/1e6):+.2f} M€", delta_color="normal" if tr_list[-1] >= 0 else "inverse")

    gp_c1, gp_c2 = st.columns(2)
    with gp_c1:
        st.markdown("**1. P&L Projeté : CA vs EBE (M€)**")
        st.line_chart(df_proj.set_index("Année")[["Chiffre d'Aff. (M€)", "EBE (M€)"]])
    with gp_c2:
        st.markdown("**2. Trajectoire Trésorerie Nette & Free Cash-Flow (M€)**")
        st.bar_chart(df_proj.set_index("Année")[["Free Cash-Flow (M€)", "Trésorerie (M€)"]], color=["#059669", "#2563eb"])

    st.dataframe(df_proj, hide_index=True, use_container_width=True)

    feuille_route = [
        f"1. Création de valeur (EBE) : Le plan de croissance amène l'EBE à {ebe_list[-1]:.2f} M€ en 2030 (marge de {(ebe_list[-1]/p_list[-1]*100):.1f} % vs cible sectorielle {normes['ebe_cible']} %).",
        f"2. Exigence de Cash (Free Cash-Flow) : L'entreprise dégage un Free Cash-Flow cumulé de {sum(fcf_list):.2f} M€ sur 5 ans après avoir financé {ppi_annuel*5:.2f} M€ de CAPEX et absorbé la variation du BFR.",
        f"3. Profil de Dette & LBO : Le levier bancaire s'établit à {levier_list[-1]:.1f} x en 2030, sécurisant les covenants de la dette (limite {normes['levier_dette_max']} x).",
        f"4. Recommandation actionnariale : {'La génération de cash-flow permet de soutenir le service d une dette d acquisition (LBO) ou de distribuer des dividendes.' if sum(fcf_list) > 0 and levier_list[-1] <= normes['levier_dette_max'] else 'Le projet consomme du cash. Renforcer la rentabilité d exploitation avant toute distribution ou dette LBO.'}"
    ]

    pdf_pro_bytes = build_pdf_ent_prospective(entreprise, normes, ppi_annuel, df_proj, fcf_list[-1], feuille_route)
    st.download_button("📑 Télécharger le Business Plan à 5 ans (PDF 1 page)", data=pdf_pro_bytes, file_name=f"Business_Plan_5_ans_{entreprise.replace(' ','_')}.pdf", mime="application/pdf", type="primary")

    afficher_synthese_unique(
        "Soutenabilité du Business Plan & Capacité LBO",
        f"À l'horizon 2030, sous l'effet conjugué des prix et des volumes, l'entreprise atteint {p_list[-1]:.2f} M€ de chiffre d'affaires et {ebe_list[-1]:.2f} M€ d'EBE. Le levier d'endettement net tombe à {levier_list[-1]:.1f} x.",
        f"Le projet génère {sum(fcf_list):.2f} M€ de Free Cash-Flow cumulé sur la période, validant la capacité intrinsèque de l'entreprise à s'autofinancer après ses {ppi_annuel:.2f} M€/an de CAPEX.",
        f"{'Le modèle est parfaitement bancable pour une opération de LBO ou de croissance externe structurante.' if (tr_list[-1] > 0 and levier_list[-1] <= normes['levier_dette_max']) else 'Le plan de développement tend la trésorerie. Il est indispensable d améliorer la marge EBE ou d allonger la durée de la dette d investissement.'}"
    )
