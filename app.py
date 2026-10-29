# -*- coding: utf-8 -*-
"""StockFlow Maroc — gestion de stock, fournisseurs, mouvements et analyses.

Modèle de sécurité (multi-utilisateurs)
---------------------------------------
1. Authentification : Supabase Auth (email + mot de passe), via `supabase_auth.py`.
   Aucune page ni aucune donnée n'est accessible sans session valide.
2. Isolation des données : PostgreSQL Row Level Security (voir
   `migrations/001_auth_rls.sql`). Chaque ligne porte un `user_id` et les
   politiques n'autorisent que `user_id = auth.uid()`.
3. Toutes les requêtes passent par Supabase REST/PostgREST avec le jeton
   utilisateur : Supabase valide le JWT avant que PostgreSQL applique la RLS.
   Les opérations critiques multi-étapes sont des RPC SECURITY INVOKER.
4. Secrets : seuls SUPABASE_URL et SUPABASE_ANON_KEY (clé publique) sont utilisés.
   La clé `service_role` et `DATABASE_URL` ne sont jamais utilisés.
"""
from __future__ import annotations

import io
import logging
import os
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

import supabase_auth as sa

APP_NAME = "StockFlow Maroc"
CURRENCY = "DH"
MOVE_IN, MOVE_OUT = "Entrée", "Sortie"
LOW_DAYS = 30

log = logging.getLogger("stockflow")

NOT_FOUND_MSG = "Élément introuvable ou accès refusé."
REQUIRED_TABLES = ("suppliers", "products", "movements")


class SafeError(RuntimeError):
    """Erreur dont le message peut être affiché tel quel à l'utilisateur."""


# ---------------------------------------------------------------------------
# Configuration / secrets
# ---------------------------------------------------------------------------
def get_secret(name: str) -> str:
    """Read a value from Streamlit Secrets, with env fallback for local use."""
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value or os.environ.get(name, "") or "").strip()


def auth_config() -> sa.AuthConfig:
    return sa.build_config(get_secret("SUPABASE_URL"), get_secret("SUPABASE_ANON_KEY"))


class RestError(RuntimeError):
    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


USER_ERRORS = (ValueError, RestError, PermissionError, sa.AuthError)


def _rest_request(method, path, *, params=None, payload=None, prefer=None):
    """PostgREST calls use the user's access token; Supabase validates JWT before RLS."""
    cfg = auth_config()
    auth, notice = sa.ensure_session(cfg, st.session_state)
    session = st.session_state.get(sa.SESSION_KEY) or {}
    token = session.get("access_token")
    if not auth or not token:
        raise PermissionError("Session invalide.")
    headers = {"apikey": cfg.anon_key, "Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    try:
        response = requests.request(method, cfg.url + "/rest/v1/" + path,
                                    headers=headers, params=params, json=payload, timeout=20)
    except requests.RequestException as exc:
        raise RestError("Supabase est temporairement inaccessible.") from exc
    try:
        data = response.json() if response.content else None
    except ValueError:
        data = None
    if not response.ok:
        info = data if isinstance(data, dict) else {}
        code = str(info.get("code") or "")
        msg = str(info.get("message") or "")
        if response.status_code == 401:
            st.session_state.pop(sa.SESSION_KEY, None)
            raise PermissionError("Session expirée. Reconnectez-vous.")
        if code == "23505":
            msg = "Cet élément existe déjà dans votre espace (référence ou nom en double)."
        elif code == "23503":
            msg = "Lien invalide : fournisseur ou produit introuvable dans votre espace."
        elif code == "23514":
            msg = "Valeurs refusées (quantités et prix doivent être positifs)."
        elif response.status_code in (401, 403):
            msg = "Action non autorisée."
        else:
            msg = "Erreur de base de données. Vérifiez la migration et réessayez."
        raise RestError(msg, response.status_code, code)
    return data


def _rpc(name, payload):
    return _rest_request("POST", "rpc/" + name, payload=payload, prefer="return=representation")


def current_user_id() -> str:
    return sa.current_user_id(st.session_state)


def check_schema():
    """Verify the multi-user migration using an authenticated PostgREST RPC."""
    ready = _rpc("stockflow_api_ready", {})
    if ready is not True:
        raise SafeError("La base n'est pas prête. Exécutez migrations/001_auth_rls.sql puis migrations/002_rest_rpc.sql dans Supabase SQL Editor.")


def db_error_message(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, PermissionError):
        return "Accès refusé ou session expirée. Reconnectez-vous."
    if isinstance(exc, RestError):
        return str(exc)
    log.exception("Erreur de base de données", exc_info=exc)
    return "Erreur de base de données. Réessayez ; si le problème persiste, contactez l'administrateur."

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def rows_df(data) -> pd.DataFrame:
    return pd.DataFrame(data or [])


def _require_row(data):
    if not data:
        raise ValueError(NOT_FOUND_MSG)


def get_products():
    data = _rest_request("GET", "products", params={
        "select": "id,reference,name,quantity,purchase_price,selling_price,min_stock,supplier_id,created_at,suppliers(name)",
        "order": "name.asc"})
    flat = []
    for r in data or []:
        sup = r.pop("suppliers", None)
        r["supplier_name"] = (sup or {}).get("name", "") if isinstance(sup, dict) else ""
        flat.append(r)
    df = rows_df(flat)
    if df.empty:
        for col in ["stock_value", "sale_value", "profit", "status"]:
            df[col] = pd.Series(dtype="object")
        return df
    df["stock_value"] = (df.quantity * df.purchase_price).round(2)
    df["sale_value"] = (df.quantity * df.selling_price).round(2)
    df["profit"] = (df.sale_value - df.stock_value).round(2)
    df["status"] = "OK"
    df.loc[df.quantity <= df.min_stock, "status"] = "Stock faible"
    df.loc[df.quantity <= 0, "status"] = "Rupture"
    return df


def get_movements():
    data = _rest_request("GET", "movements", params={
        "select": "id,product_id,product_ref,product_name,type,quantity,unit_price,total_value,note,created_at,products(reference,name)",
        "order": "created_at.desc,id.desc"})
    flat = []
    for r in data or []:
        product = r.pop("products", None)
        r["reference"] = (product or {}).get("reference") or r.get("product_ref")
        r["name"] = (product or {}).get("name") or r.get("product_name")
        flat.append(r)
    return rows_df(flat)


def get_suppliers():
    suppliers = _rest_request("GET", "suppliers", params={"select": "id,name,phone,email,notes,created_at", "order": "name.asc"}) or []
    products = _rest_request("GET", "products", params={"select": "supplier_id"}) or []
    counts = {}
    for p in products:
        if p.get("supplier_id") is not None:
            k = int(p["supplier_id"]); counts[k] = counts.get(k, 0) + 1
    for supplier in suppliers:
        supplier["products_count"] = counts.get(int(supplier["id"]), 0)
    return rows_df(suppliers)


def add_supplier(name, phone, email, notes):
    name = name.strip()
    if not name:
        raise ValueError("Le nom du fournisseur est obligatoire.")
    _rest_request("POST", "suppliers", payload={"name": name, "phone": phone.strip(),
        "email": email.strip(), "notes": notes.strip(), "created_at": now_str()}, prefer="return=minimal")


def update_supplier(sid, name, phone, email, notes):
    if not name.strip():
        raise ValueError("Le nom du fournisseur est obligatoire.")
    data = _rest_request("PATCH", "suppliers", params={"id": f"eq.{int(sid)}", "select": "id"},
        payload={"name": name.strip(), "phone": phone.strip(), "email": email.strip(), "notes": notes.strip()}, prefer="return=representation")
    _require_row(data)


def delete_supplier(sid):
    data = _rest_request("DELETE", "suppliers", params={"id": f"eq.{int(sid)}", "select": "id"}, prefer="return=representation")
    _require_row(data)


def add_product(ref, name, qty, buy, sell, min_stock, supplier_id):
    ref, name = ref.strip(), name.strip()
    if not ref or not name:
        raise ValueError("La référence et le nom du produit sont obligatoires.")
    if int(qty) < 0 or int(min_stock) < 0 or float(buy) < 0 or float(sell) < 0:
        raise ValueError("Quantités et prix doivent être positifs ou nuls.")
    _rpc("stockflow_add_product", {"p_reference": ref, "p_name": name, "p_quantity": int(qty),
        "p_purchase_price": float(buy), "p_selling_price": float(sell), "p_min_stock": int(min_stock),
        "p_supplier_id": int(supplier_id) if supplier_id not in (None, "", 0) else None,
        "p_created_at": now_str()})


def update_product(pid, ref, name, buy, sell, min_stock, supplier_id):
    if not ref.strip() or not name.strip():
        raise ValueError("La référence et le nom sont obligatoires.")
    if float(buy) < 0 or float(sell) < 0 or int(min_stock) < 0:
        raise ValueError("Quantités et prix doivent être positifs ou nuls.")
    data = _rest_request("PATCH", "products", params={"id": f"eq.{int(pid)}", "select": "id"}, payload={
        "reference": ref.strip(), "name": name.strip(), "purchase_price": float(buy), "selling_price": float(sell),
        "min_stock": int(min_stock), "supplier_id": int(supplier_id) if supplier_id not in (None, "", 0) else None},
        prefer="return=representation")
    _require_row(data)


def delete_product(pid):
    data = _rest_request("DELETE", "products", params={"id": f"eq.{int(pid)}", "select": "id"}, prefer="return=representation")
    _require_row(data)


def add_movement(pid, typ, qty, note="", unit_price=None):
    qty = int(qty)
    if qty < 1:
        raise ValueError("La quantité doit être au moins 1.")
    if typ not in (MOVE_IN, MOVE_OUT):
        raise ValueError("Type de mouvement invalide.")
    result = _rpc("stockflow_add_movement", {"p_product_id": int(pid), "p_type": typ,
        "p_quantity": qty, "p_note": (note or "").strip()[:250],
        "p_unit_price": float(unit_price) if unit_price is not None else None,
        "p_created_at": now_str()})
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return int(result[0].get("new_quantity", 0))
    if isinstance(result, dict):
        return int(result.get("new_quantity", 0))
    if isinstance(result, (int, float)):
        return int(result)
    raise RestError("Réponse inattendue de Supabase pour le mouvement de stock.")

def money(x):
    return f"{float(x):,.2f} DH".replace(",", " ")


def intfmt(x):
    return f"{int(x):,}".replace(",", " ")


def csv_bytes(df):
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def excel_bytes(sheets):
    b = io.BytesIO()
    with pd.ExcelWriter(b, engine="openpyxl") as writer:
        for name, df in sheets.items():
            sheet_name = name[:31]
            df.to_excel(writer, index=False, sheet_name=sheet_name)
            ws = writer.sheets[sheet_name]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cells in ws.columns:
                width = max(len(str(cell.value or "")) for cell in cells) + 2
                ws.column_dimensions[cells[0].column_letter].width = min(max(width, 12), 40)
    return b.getvalue()


def supplier_options(suppliers):
    return {int(r.id): r.name for r in suppliers.itertuples()}


def show_df(df):
    try:
        st.dataframe(df, use_container_width=True, hide_index=True)
    except TypeError:
        st.dataframe(df, width="stretch", hide_index=True)


CSS = """
<style>
.block-container{padding-top:1.3rem;max-width:1400px}
[data-testid="stMetric"]{background:linear-gradient(140deg,rgba(28,116,176,.12),rgba(128,128,128,.035));border:1px solid rgba(128,128,128,.22);border-radius:15px;padding:14px 16px}
[data-testid="stMetricValue"]{font-size:1.55rem;font-weight:700}
.hero{padding:1.2rem 1.4rem;border:1px solid rgba(128,128,128,.22);border-radius:18px;margin-bottom:1rem;background:linear-gradient(120deg,rgba(28,116,176,.15),rgba(128,128,128,.03))}
@media(max-width:640px){.block-container{padding-left:.8rem;padding-right:.8rem}[data-testid="stMetricValue"]{font-size:1.15rem}}
</style>
"""


def dashboard():
    p, m = get_products(), get_movements()
    st.markdown(
        '<div class="hero"><h2>📊 StockFlow Maroc</h2><p>Vue globale de votre stock, des ventes estimées et des alertes — montants en dirhams marocains (DH).</p></div>',
        unsafe_allow_html=True,
    )
    if p.empty:
        st.info("Votre inventaire est vide. Ajoutez des produits dans la section Produits.")
        return
    total_cost = float(p.stock_value.sum())
    sale_value = float(p.sale_value.sum())
    potential = float(p.profit.sum())
    low = int((p.status == "Stock faible").sum())
    out = int((p.status == "Rupture").sum())
    a = st.columns(4)
    a[0].metric("📦 Références", intfmt(len(p)))
    a[1].metric("🔢 Unités en stock", intfmt(p.quantity.sum()))
    a[2].metric("⚠️ Alertes", intfmt(low + out))
    a[3].metric("💰 Valeur d'achat", money(total_cost))
    b = st.columns(3)
    b[0].metric("🏷️ Vente potentielle", money(sale_value))
    b[1].metric("📈 Marge brute potentielle", money(potential))
    b[2].metric("Marge estimée", f"{(potential / sale_value * 100 if sale_value else 0):.1f} %")
    left, right = st.columns(2)
    with left:
        st.subheader("🏆 Top 5 — Valeur immobilisée")
        top = p.nlargest(5, "stock_value").sort_values("stock_value")
        if not top.empty:
            fig = px.bar(top, x="stock_value", y="name", orientation="h",
                         labels={"stock_value": "Valeur d'achat (DH)", "name": "Produit"},
                         text_auto=".2s")
            fig.update_layout(height=340, margin=dict(l=5, r=10, t=20, b=5),
                              yaxis_title="", xaxis_title="Valeur (DH)")
            st.plotly_chart(fig, use_container_width=True)
    with right:
        st.subheader("📉 Valeur des sorties / bénéfice brut estimé")
        sales = m[m.type == MOVE_OUT].copy() if not m.empty else pd.DataFrame()
        if not sales.empty:
            sales["date"] = pd.to_datetime(sales.created_at, errors="coerce").dt.date
            costs = p.set_index("id")["purchase_price"].to_dict()
            sales["gross_profit"] = [
                float(r.total_value) - float(r.quantity) * float(costs.get(r.product_id, 0))
                for r in sales.itertuples()
            ]
            daily = sales.groupby("date", as_index=False).agg(
                **{"Ventes (DH)": ("total_value", "sum"),
                   "Bénéfice brut estimé (DH)": ("gross_profit", "sum")}
            )
            long = daily.melt(id_vars="date", var_name="Indicateur", value_name="Montant (DH)")
            fig = px.line(long, x="date", y="Montant (DH)", color="Indicateur", markers=True)
            fig.update_layout(height=340, margin=dict(l=5, r=10, t=20, b=5),
                              xaxis_title="Date", yaxis_title="DH", legend_title="")
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Le bénéfice est estimatif : il utilise le prix d'achat actuel du produit.")
        else:
            st.info("Les sorties enregistrées apparaîtront ici.")
    st.subheader("🚨 Produits à surveiller")
    alerts = p[p.status != "OK"].copy()
    if alerts.empty:
        st.success("Aucun produit en alerte de stock.")
    else:
        alerts["Statut"] = alerts.status.map({"Rupture": "🔴 Rupture", "Stock faible": "🟠 Stock faible"})
        show_df(alerts[["reference", "name", "quantity", "min_stock", "supplier_name", "Statut"]].rename(
            columns={"reference": "Référence", "name": "Produit", "quantity": "Stock",
                     "min_stock": "Seuil", "supplier_name": "Fournisseur"}))
    st.subheader(f"🕰️ Stock dormant — aucune activité depuis {LOW_DAYS} jours")
    last = {}
    if not m.empty:
        for r in m.itertuples():
            if pd.notna(r.product_id) and r.product_id not in last:
                last[r.product_id] = r.created_at
    p = p.copy()
    p["last_movement"] = p.apply(lambda r: last.get(r.id, r.created_at), axis=1)
    p["last_dt"] = pd.to_datetime(p.last_movement, errors="coerce")
    dormant = p[(p.quantity > 0) & (p.last_dt < pd.Timestamp(datetime.now() - timedelta(days=LOW_DAYS)))]
    if dormant.empty:
        st.success("Aucun stock dormant détecté selon le seuil actuel.")
    else:
        show_df(dormant[["reference", "name", "quantity", "last_movement", "supplier_name"]].rename(
            columns={"reference": "Référence", "name": "Produit", "quantity": "Stock",
                     "last_movement": "Dernier mouvement", "supplier_name": "Fournisseur"}))
    st.subheader("🕑 Derniers mouvements")
    if m.empty:
        st.caption("Aucun mouvement enregistré.")
    else:
        show_df(m.head(8)[["created_at", "reference", "name", "type", "quantity",
                           "unit_price", "total_value", "note"]].rename(
            columns={"created_at": "Date", "reference": "Référence", "name": "Produit",
                     "type": "Type", "quantity": "Qté", "unit_price": "Prix unitaire (DH)",
                     "total_value": "Total (DH)", "note": "Note"}))


def products_page():
    st.title("📦 Gestion des produits")
    p, s = get_products(), get_suppliers()
    opts = supplier_options(s)
    supplier_names = {v: k for k, v in opts.items()}
    t1, t2, t3 = st.tabs(["Inventaire", "Ajouter un produit", "Modifier / supprimer"])
    with t1:
        if p.empty:
            st.info("Aucun produit pour le moment.")
        else:
            q = st.text_input("Rechercher par référence ou nom", key="prod_search")
            view = p.copy()
            if q:
                view = view[view.reference.str.contains(q, case=False, na=False) |
                            view.name.str.contains(q, case=False, na=False)]
            show_df(view[["reference", "name", "quantity", "purchase_price", "selling_price",
                          "min_stock", "supplier_name", "stock_value", "profit", "status"]].rename(
                columns={"reference": "Référence", "name": "Produit", "quantity": "Stock",
                         "purchase_price": "Prix achat (DH)", "selling_price": "Prix vente (DH)",
                         "min_stock": "Seuil", "supplier_name": "Fournisseur",
                         "stock_value": "Valeur stock (DH)", "profit": "Bénéfice potentiel (DH)",
                         "status": "Statut"}))
            st.download_button("Télécharger inventaire CSV", csv_bytes(view),
                               f"inventaire_stockflow_{datetime.now():%Y%m%d}.csv", "text/csv")
    with t2:
        with st.form("add_product_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            ref, name = c1.text_input("Référence *"), c2.text_input("Nom du produit *")
            c3, c4, c5 = st.columns(3)
            qty = c3.number_input("Stock initial", 0, 1_000_000, 0)
            buy = c4.number_input("Prix d'achat (DH)", 0.0, 10_000_000.0, 0.0, step=1.0)
            sell = c5.number_input("Prix de vente (DH)", 0.0, 10_000_000.0, 0.0, step=1.0)
            c6, c7 = st.columns(2)
            minimum = c6.number_input("Seuil d'alerte", 0, 1_000_000, 5)
            sn = c7.selectbox("Fournisseur", ["— Aucun —"] + list(supplier_names.keys()))
            submit = st.form_submit_button("Ajouter le produit", type="primary")
        if submit:
            try:
                add_product(ref, name, qty, buy, sell, minimum, supplier_names.get(sn))
                st.success("Produit ajouté.")
                st.rerun()
            except USER_ERRORS as e:
                st.error(f"Impossible d'ajouter le produit : {db_error_message(e)}")
    with t3:
        if p.empty:
            st.info("Aucun produit à modifier.")
        else:
            labels = {int(r.id): f"{r.reference} — {r.name}" for r in p.itertuples()}
            pid = st.selectbox("Choisir un produit", list(labels), format_func=labels.get)
            r = p[p.id == pid].iloc[0]
            with st.form(f"edit_product_{pid}"):
                c1, c2 = st.columns(2)
                ref = c1.text_input("Référence", str(r.reference))
                name = c2.text_input("Nom", str(r["name"]))
                c3, c4, c5 = st.columns(3)
                buy = c3.number_input("Prix d'achat", 0.0, 10_000_000.0, float(r.purchase_price), step=1.0)
                sell = c4.number_input("Prix de vente", 0.0, 10_000_000.0, float(r.selling_price), step=1.0)
                minimum = c5.number_input("Seuil d'alerte", 0, 1_000_000, int(r.min_stock))
                supplier_list = ["— Aucun —"] + list(supplier_names.keys())
                cur_supplier = opts.get(int(r.supplier_id)) if pd.notna(r.supplier_id) else None
                sn = st.selectbox("Fournisseur", supplier_list,
                                  index=supplier_list.index(cur_supplier) if cur_supplier in supplier_names else 0)
                save = st.form_submit_button("Enregistrer les modifications", type="primary")
            if save:
                try:
                    update_product(pid, ref, name, buy, sell, minimum, supplier_names.get(sn))
                    st.success("Produit modifié.")
                    st.rerun()
                except USER_ERRORS as e:
                    st.error(f"Modification impossible : {db_error_message(e)}")
            st.divider()
            confirm = st.checkbox("Je confirme la suppression définitive", key=f"confirm_del_{pid}")
            if st.button("Supprimer ce produit", disabled=not confirm):
                try:
                    delete_product(pid)
                    st.success("Produit supprimé.")
                    st.rerun()
                except USER_ERRORS as e:
                    st.error(db_error_message(e))


def movements_page():
    st.title("🔄 Mouvements de stock")
    p, m = get_products(), get_movements()
    t1, t2 = st.tabs(["Nouvelle entrée / sortie", "Historique"])
    with t1:
        if p.empty:
            st.info("Ajoutez d'abord un produit.")
        else:
            labels = {int(r.id): f"{r.reference} — {r['name']} (stock : {r.quantity})" for _, r in p.iterrows()}
            pid = st.selectbox("Produit", list(labels), format_func=labels.get, key="movement_product")
            r = p[p.id == pid].iloc[0]
            with st.form("movement_form", clear_on_submit=True):
                typ = st.radio("Type", [MOVE_IN, MOVE_OUT], horizontal=True)
                qty = st.number_input("Quantité", 1, 1_000_000, 1)
                default_price = float(r.purchase_price if typ == MOVE_IN else r.selling_price)
                price = st.number_input("Prix unitaire (DH)", 0.0, 10_000_000.0, default_price,
                                        step=1.0, help="Pour une sortie, utilisez le prix de vente réel si vous le connaissez.")
                note = st.text_input("Note (livraison, vente, retour...)")
                submit = st.form_submit_button("Enregistrer le mouvement", type="primary")
            if submit:
                try:
                    newqty = add_movement(pid, typ, qty, note, price)
                    st.success(f"Mouvement enregistré. Nouveau stock : {newqty} unité(s).")
                    st.rerun()
                except USER_ERRORS as e:
                    st.error(db_error_message(e))
    with t2:
        m = get_movements()
        if m.empty:
            st.info("Aucun mouvement enregistré.")
        else:
            c1, c2, c3 = st.columns(3)
            product = c1.selectbox("Produit", ["Tous"] + sorted((m.reference + " — " + m.name).unique().tolist()))
            typ = c2.selectbox("Type", ["Tous", MOVE_IN, MOVE_OUT])
            period = c3.selectbox("Période", ["Tout", "Aujourd'hui", "7 jours", "30 jours"])
            v = m.copy()
            if product != "Tous":
                v = v[(v.reference + " — " + v.name) == product]
            if typ != "Tous":
                v = v[v.type == typ]
            now = datetime.now()
            cutoff = {"Aujourd'hui": now.replace(hour=0, minute=0, second=0, microsecond=0),
                      "7 jours": now - timedelta(days=7),
                      "30 jours": now - timedelta(days=30)}.get(period)
            if cutoff:
                v = v[pd.to_datetime(v.created_at, errors="coerce") >= cutoff]
            show_df(v[["created_at", "reference", "name", "type", "quantity", "unit_price",
                       "total_value", "note"]].rename(
                columns={"created_at": "Date", "reference": "Référence", "name": "Produit",
                         "type": "Type", "quantity": "Quantité", "unit_price": "Prix unitaire (DH)",
                         "total_value": "Total (DH)", "note": "Note"}))
            st.download_button("Exporter l'historique CSV", csv_bytes(v),
                               f"mouvements_{datetime.now():%Y%m%d}.csv", "text/csv")


def suppliers_page():
    st.title("🤝 Fournisseurs")
    s = get_suppliers()
    _p = get_products()
    t1, t2, t3 = st.tabs(["Liste des fournisseurs", "Ajouter", "Modifier / supprimer"])
    with t1:
        if s.empty:
            st.info("Aucun fournisseur enregistré.")
        else:
            show_df(s[["name", "phone", "email", "products_count", "notes"]].rename(
                columns={"name": "Fournisseur", "phone": "Téléphone", "email": "Email",
                         "products_count": "Produits liés", "notes": "Notes"}))
    with t2:
        with st.form("add_supplier_form", clear_on_submit=True):
            name = st.text_input("Nom du fournisseur *")
            c1, c2 = st.columns(2)
            phone, email = c1.text_input("Téléphone"), c2.text_input("Email")
            notes = st.text_area("Notes / produits proposés")
            submit = st.form_submit_button("Ajouter fournisseur", type="primary")
        if submit:
            try:
                add_supplier(name, phone, email, notes)
                st.success("Fournisseur ajouté.")
                st.rerun()
            except USER_ERRORS as e:
                st.error(f"Ajout impossible : {db_error_message(e)}")
    with t3:
        if s.empty:
            st.info("Aucun fournisseur à modifier.")
        else:
            labels = {int(r.id): r.name for r in s.itertuples()}
            sid = st.selectbox("Fournisseur", list(labels), format_func=labels.get)
            r = s[s.id == sid].iloc[0]
            with st.form(f"edit_supplier_{sid}"):
                name = st.text_input("Nom", str(r["name"]))
                c1, c2 = st.columns(2)
                phone, email = c1.text_input("Téléphone", str(r.phone or "")), c2.text_input("Email", str(r.email or ""))
                notes = st.text_area("Notes", str(r.notes or ""))
                save = st.form_submit_button("Enregistrer", type="primary")
            if save:
                try:
                    update_supplier(sid, name, phone, email, notes)
                    st.success("Fournisseur modifié.")
                    st.rerun()
                except USER_ERRORS as e:
                    st.error(f"Modification impossible : {db_error_message(e)}")
            st.caption("Si vous supprimez un fournisseur, les produits liés restent dans l'inventaire mais sans fournisseur.")
            confirm = st.checkbox("Confirmer la suppression", key=f"confirm_supplier_{sid}")
            if st.button("Supprimer le fournisseur", disabled=not confirm):
                try:
                    delete_supplier(sid)
                    st.success("Fournisseur supprimé.")
                    st.rerun()
                except USER_ERRORS as e:
                    st.error(db_error_message(e))


def backup_page():
    st.title("💾 Sauvegarde & données")
    p, m, s = get_products(), get_movements(), get_suppliers()
    inv = p.rename(columns={"reference": "Référence", "name": "Produit", "quantity": "Stock",
        "purchase_price": "Prix achat (DH)", "selling_price": "Prix vente (DH)", "min_stock": "Seuil",
        "supplier_name": "Fournisseur", "stock_value": "Valeur stock (DH)",
        "sale_value": "Vente potentielle (DH)", "profit": "Bénéfice potentiel (DH)", "status": "Statut"})
    hist = m.rename(columns={"created_at": "Date", "reference": "Référence", "name": "Produit",
        "type": "Type", "quantity": "Quantité", "unit_price": "Prix unitaire (DH)",
        "total_value": "Total (DH)", "note": "Note"})
    sup = s.rename(columns={"name": "Fournisseur", "phone": "Téléphone", "email": "Email",
        "notes": "Notes", "products_count": "Produits liés", "created_at": "Créé le"})
    st.write("Téléchargez une copie des produits, des mouvements et des fournisseurs.")
    st.caption("Ces fichiers ne contiennent que les données de votre compte.")
    st.download_button("Télécharger la sauvegarde Excel complète",
        excel_bytes({"Inventaire": inv, "Mouvements": hist, "Fournisseurs": sup}),
        f"stockflow_backup_{datetime.now():%Y%m%d_%H%M}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    st.download_button("Télécharger l'inventaire CSV", csv_bytes(inv), "stockflow_inventaire.csv", "text/csv")
    st.download_button("Télécharger les mouvements CSV", csv_bytes(hist), "stockflow_mouvements.csv", "text/csv")
    st.download_button("Télécharger les fournisseurs CSV", csv_bytes(sup), "stockflow_fournisseurs.csv", "text/csv")
    st.divider()
    st.subheader("☁️ Base de données PostgreSQL (Supabase)")
    st.success("Les données sont enregistrées dans Supabase via l’API authentifiée. Elles ne dépendent pas du fichier local SQLite.")
    st.caption("Pensez à télécharger régulièrement une sauvegarde Excel.")


# ---------------------------------------------------------------------------
# Authentification (interface)
# ---------------------------------------------------------------------------
def auth_page(cfg: sa.AuthConfig, notice: str | None = None):
    """Page de connexion / inscription — la SEULE page visible sans session."""
    st.markdown(
        '<div class="hero"><h2>📦 StockFlow Maroc</h2>'
        "<p>Gestion de stock sécurisée : chaque compte dispose de son propre espace privé "
        "(produits, fournisseurs et mouvements). Montants en dirhams marocains (DH).</p></div>",
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        if notice:
            st.warning(notice)
        flash = st.session_state.pop("auth_notice", None)
        if flash:
            st.info(flash)
        tab_in, tab_up = st.tabs(["🔐 Connexion", "📝 Créer un compte"])
        with tab_in:
            with st.form("login_form"):
                email = st.text_input("Email")
                password = st.text_input("Mot de passe", type="password")
                submit = st.form_submit_button("Se connecter", type="primary")
            if submit:
                try:
                    session = sa.sign_in(cfg, email, password)
                except sa.AuthError as e:
                    st.error(e.message)
                else:
                    sa.start_session(st.session_state, session)
                    st.rerun()
        with tab_up:
            with st.form("signup_form"):
                new_email = st.text_input("Email", key="signup_email")
                new_password = st.text_input(
                    f"Mot de passe (minimum {sa.MIN_PASSWORD_LENGTH} caractères)", type="password")
                confirm_password = st.text_input("Confirmer le mot de passe", type="password")
                create = st.form_submit_button("Créer mon compte", type="primary")
            if create:
                if new_password != confirm_password:
                    st.error("Les deux mots de passe ne correspondent pas.")
                else:
                    try:
                        session = sa.sign_up(cfg, new_email, new_password)
                    except sa.AuthError as e:
                        st.error(e.message)
                    else:
                        if session:
                            sa.start_session(st.session_state, session)
                            st.rerun()
                        # Message volontairement identique que l'adresse soit déjà inscrite ou non
                        # (évite de révéler quelles adresses ont un compte).
                        st.success(
                            "Demande enregistrée. Si la confirmation par email est activée, cliquez sur "
                            "le lien reçu puis connectez-vous depuis l'onglet « Connexion ». "
                            "Si vous avez déjà un compte, connectez-vous directement."
                        )
        st.caption("🔒 Vos données sont privées : personne d'autre ne peut les voir ni les modifier.")


def logout(cfg: sa.AuthConfig):
    sa.sign_out(cfg, (st.session_state.get(sa.SESSION_KEY) or {}).get("access_token"))
    st.session_state.clear()  # aucune donnée de cet utilisateur ne reste en mémoire
    st.session_state["auth_notice"] = "Vous avez été déconnecté."
    st.rerun()


def main():
    st.set_page_config(page_title=APP_NAME, page_icon="📦", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    # --- 1. Authentification : barrière unique devant TOUT le reste -------------
    try:
        cfg = auth_config()
    except sa.AuthError as e:
        st.error(e.message)
        st.stop()
    auth, notice = sa.ensure_session(cfg, st.session_state)
    if auth is None:
        auth_page(cfg, notice)
        st.stop()  # rien d'autre n'est exécuté sans session valide

    # --- 2. Base de données prête ? (migration appliquée) -----------------------
    if not st.session_state.get("schema_ok"):
        try:
            check_schema()
        except SafeError as e:
            st.error(str(e))
            st.stop()
        except (RestError, OSError, PermissionError, sa.AuthError) as e:
            log.exception("Connexion à la base impossible", exc_info=e)
            st.error("Impossible de se connecter à la base de données.")
            st.info("Vérifiez Streamlit Cloud > Settings > Secrets (SUPABASE_URL, SUPABASE_ANON_KEY) et que les migrations SQL ont été appliquées. "
                    "Le détail technique est dans les logs de l'application.")
            st.stop()
        st.session_state["schema_ok"] = True

    st.sidebar.markdown(f"## 📦 {APP_NAME}")
    st.sidebar.caption("Gestion de stock pour les petites entreprises marocaines")
    pages = {
        "📊 Dashboard": dashboard,
        "📦 Produits": products_page,
        "🔄 Mouvements de stock": movements_page,
        "🤝 Fournisseurs": suppliers_page,
        "💾 Sauvegarde & données": backup_page,
    }
    page = st.sidebar.radio("Navigation", list(pages), label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption(f"👤 Connecté : {auth['email']}")
    if st.sidebar.button("🚪 Se déconnecter", key="logout_button"):
        logout(cfg)
    st.sidebar.caption("Base de données : Supabase (JWT + RLS)")
    try:
        p, m, s = get_products(), get_movements(), get_suppliers()
        st.sidebar.download_button(
            "⬇️ Sauvegarde Excel",
            excel_bytes({"Inventaire": p, "Mouvements": m, "Fournisseurs": s}),
            f"stockflow_backup_{datetime.now():%Y%m%d}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        pages[page]()
    except (RestError, PermissionError, sa.AuthError) as e:
        st.error(db_error_message(e))


if __name__ == "__main__":
    main()