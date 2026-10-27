# -*- coding: utf-8 -*-
"""
StockFlow Maroc - Gestion de stock pour les petites entreprises marocaines.

Lancement :  streamlit run app.py

- Interface en français, montants en dirhams marocains (DH)
- Base de données SQLite locale (fichier "stockflow.db" créé à côté de app.py)
- Aucune API ni service payant
"""

from __future__ import annotations

import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
APP_NAME = "StockFlow Maroc"
CURRENCY = "DH"
DB_PATH = Path(
    os.environ.get("STOCKFLOW_DB") or Path(__file__).resolve().with_name("stockflow.db")
)

MOVE_IN = "Entrée"
MOVE_OUT = "Sortie"
DEFAULT_MIN_STOCK = 5
MAX_QTY = 1_000_000
MAX_PRICE = 10_000_000.0

STATUS_OK = "OK"
STATUS_LOW = "Stock faible"
STATUS_OUT = "Rupture"
STATUS_ICONS = {STATUS_OK: "🟢 OK", STATUS_LOW: "🟠 Stock faible", STATUS_OUT: "🔴 Rupture"}


class StockError(Exception):
    """Erreur métier : le message est affichable tel quel à l'utilisateur."""


# --------------------------------------------------------------------------- #
# Base de données
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reference      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name           TEXT    NOT NULL,
    quantity       INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    purchase_price REAL    NOT NULL DEFAULT 0 CHECK (purchase_price >= 0),
    selling_price  REAL    NOT NULL DEFAULT 0 CHECK (selling_price >= 0),
    min_stock      INTEGER NOT NULL DEFAULT 5 CHECK (min_stock >= 0),
    created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS movements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_ref  TEXT    NOT NULL,
    product_name TEXT    NOT NULL,
    type         TEXT    NOT NULL CHECK (type IN ('Entrée', 'Sortie')),
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_movements_date    ON movements(created_at);
CREATE INDEX IF NOT EXISTS idx_movements_product ON movements(product_id);
"""


@contextmanager
def get_conn():
    """Connexion SQLite avec commit/rollback automatiques."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _to_int(value, label: str, minimum: int, maximum: int = MAX_QTY) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise StockError(f"{label} doit être un nombre entier.") from None
    if not math.isfinite(number) or number != int(number):
        raise StockError(f"{label} doit être un nombre entier.")
    number = int(number)
    if number < minimum:
        raise StockError(f"{label} doit être au minimum {minimum}.")
    if number > maximum:
        raise StockError(f"{label} ne peut pas dépasser {maximum:,}.".replace(",", " "))
    return number


def _to_price(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise StockError(f"{label} doit être un nombre.") from None
    if not math.isfinite(number) or number < 0:
        raise StockError(f"{label} doit être un nombre positif ou nul.")
    if number > MAX_PRICE:
        raise StockError(f"{label} est trop élevé.")
    return round(number, 2)


def validate_product(reference, name, quantity, purchase_price, selling_price, min_stock) -> dict:
    """Nettoie et valide les champs d'un produit. `quantity=None` = non concerné."""
    reference = (reference or "").strip()
    name = (name or "").strip()
    if not reference:
        raise StockError("La référence est obligatoire.")
    if len(reference) > 50:
        raise StockError("La référence est trop longue (50 caractères maximum).")
    if not name:
        raise StockError("Le nom du produit est obligatoire.")
    if len(name) > 120:
        raise StockError("Le nom du produit est trop long (120 caractères maximum).")

    data = {
        "reference": reference,
        "name": name,
        "purchase_price": _to_price(purchase_price, "Le prix d'achat"),
        "selling_price": _to_price(selling_price, "Le prix de vente"),
        "min_stock": _to_int(min_stock, "Le seuil d'alerte", 0),
    }
    if quantity is not None:
        data["quantity"] = _to_int(quantity, "La quantité", 0)
    return data


# --------------------------------------------------------------------------- #
# Opérations sur les produits et le stock
# --------------------------------------------------------------------------- #
def _insert_movement(conn, product_id, reference, name, move_type, quantity, note) -> None:
    conn.execute(
        """INSERT INTO movements
           (product_id, product_ref, product_name, type, quantity, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (product_id, reference, name, move_type, quantity, note, now_str()),
    )


def _integrity_message(exc: sqlite3.IntegrityError, reference: str) -> str:
    if "UNIQUE" in str(exc).upper():
        return f"La référence « {reference} » existe déjà."
    return "Données invalides : vérifiez les valeurs saisies."


def add_product(reference, name, quantity, purchase_price, selling_price,
                min_stock=DEFAULT_MIN_STOCK) -> int:
    data = validate_product(reference, name, quantity, purchase_price, selling_price, min_stock)
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO products
                   (reference, name, quantity, purchase_price, selling_price, min_stock, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (data["reference"], data["name"], data["quantity"], data["purchase_price"],
                 data["selling_price"], data["min_stock"], now_str()),
            )
            product_id = cur.lastrowid
            if data["quantity"] > 0:
                _insert_movement(conn, product_id, data["reference"], data["name"],
                                 MOVE_IN, data["quantity"], "Stock initial")
    except sqlite3.IntegrityError as exc:
        raise StockError(_integrity_message(exc, data["reference"])) from None
    return product_id


def update_product(product_id, reference, name, purchase_price, selling_price, min_stock) -> None:
    data = validate_product(reference, name, None, purchase_price, selling_price, min_stock)
    try:
        with get_conn() as conn:
            cur = conn.execute(
                """UPDATE products
                   SET reference = ?, name = ?, purchase_price = ?, selling_price = ?, min_stock = ?
                   WHERE id = ?""",
                (data["reference"], data["name"], data["purchase_price"],
                 data["selling_price"], data["min_stock"], int(product_id)),
            )
            if cur.rowcount == 0:
                raise StockError("Produit introuvable (il a peut-être été supprimé).")
    except sqlite3.IntegrityError as exc:
        raise StockError(_integrity_message(exc, data["reference"])) from None


def delete_product(product_id) -> None:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM products WHERE id = ?", (int(product_id),))
        if cur.rowcount == 0:
            raise StockError("Produit introuvable (il a peut-être déjà été supprimé).")


def add_movement(product_id, move_type, quantity, note="") -> int:
    """Enregistre une entrée ou une sortie de stock. Retourne le nouveau stock."""
    if move_type not in (MOVE_IN, MOVE_OUT):
        raise StockError("Type de mouvement invalide.")
    qty = _to_int(quantity, "La quantité", 1)
    note = (note or "").strip()[:200]

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")  # verrou en écriture : évite les accès concurrents
        row = conn.execute(
            "SELECT reference, name, quantity FROM products WHERE id = ?", (int(product_id),)
        ).fetchone()
        if row is None:
            raise StockError("Produit introuvable (il a peut-être été supprimé).")

        current = row["quantity"]
        new_qty = current + qty if move_type == MOVE_IN else current - qty
        if new_qty < 0:
            raise StockError(
                f"Stock insuffisant : {current} unité(s) disponible(s), "
                f"sortie de {qty} demandée."
            )
        if new_qty > MAX_QTY:
            raise StockError("Le stock dépasserait la limite autorisée.")

        conn.execute("UPDATE products SET quantity = ? WHERE id = ?", (new_qty, int(product_id)))
        _insert_movement(conn, int(product_id), row["reference"], row["name"],
                         move_type, qty, note)
    return new_qty


# --------------------------------------------------------------------------- #
# Lecture / calculs
# --------------------------------------------------------------------------- #
def fetch_products() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT id, reference, name, quantity, purchase_price, selling_price, min_stock
               FROM products ORDER BY name COLLATE NOCASE""",
            conn,
        )
    df["stock_value"] = (df["quantity"] * df["purchase_price"]).round(2)
    df["sale_value"] = (df["quantity"] * df["selling_price"]).round(2)
    df["profit"] = (df["sale_value"] - df["stock_value"]).round(2)
    df["status"] = STATUS_OK
    df.loc[df["quantity"] <= df["min_stock"], "status"] = STATUS_LOW
    df.loc[df["quantity"] <= 0, "status"] = STATUS_OUT
    return df


def fetch_movements() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            """SELECT m.id,
                      m.created_at,
                      COALESCE(p.reference, m.product_ref) AS reference,
                      COALESCE(p.name, m.product_name)     AS name,
                      m.type, m.quantity, m.note
               FROM movements m
               LEFT JOIN products p ON p.id = m.product_id
               ORDER BY m.created_at DESC, m.id DESC""",
            conn,
        )


def inventory_summary(df: pd.DataFrame) -> dict:
    stock_value = float(df["stock_value"].sum())
    sale_value = float(df["sale_value"].sum())
    profit = sale_value - stock_value
    return {
        "products": int(len(df)),
        "units": int(df["quantity"].sum()),
        "stock_value": stock_value,
        "sale_value": sale_value,
        "profit": profit,
        "margin": (profit / sale_value * 100) if sale_value > 0 else 0.0,
        "low": int((df["status"] == STATUS_LOW).sum()),
        "out": int((df["status"] == STATUS_OUT).sum()),
    }


INVENTORY_COLUMNS = {
    "reference": "Référence",
    "name": "Produit",
    "quantity": "Quantité",
    "purchase_price": f"Prix d'achat ({CURRENCY})",
    "selling_price": f"Prix de vente ({CURRENCY})",
    "min_stock": "Seuil d'alerte",
    "stock_value": f"Valeur du stock ({CURRENCY})",
    "profit": f"Bénéfice estimé ({CURRENCY})",
    "status": "Statut",
}


def inventory_frame(df: pd.DataFrame) -> pd.DataFrame:
    return df[list(INVENTORY_COLUMNS)].rename(columns=INVENTORY_COLUMNS)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV compatible Excel français : séparateur « ; », virgule décimale, UTF-8 avec BOM."""
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def fmt_money(value: float) -> str:
    text = f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return f"{text}\u00a0{CURRENCY}"


def fmt_int(value: int) -> str:
    return f"{int(value):,}".replace(",", "\u00a0")


def seed_demo() -> None:
    """Quelques produits d'exemple pour découvrir l'application."""
    items = [
        ("HUI-001", "Huile d'olive 1 L", 40, 38.0, 52.0, 10),
        ("SUC-001", "Sucre 1 kg", 120, 7.5, 9.0, 30),
        ("THE-001", "Thé vert (boîte 200 g)", 8, 22.0, 30.0, 10),
        ("COU-001", "Couscous 1 kg", 0, 11.0, 15.0, 15),
        ("ARG-001", "Huile d'argan 100 ml", 25, 85.0, 140.0, 5),
        ("SAV-001", "Savon beldi", 60, 6.0, 12.0, 20),
        ("FAR-001", "Farine 5 kg", 4, 32.0, 42.0, 10),
    ]
    for ref, name, qty, buy, sell, minimum in items:
        try:
            add_product(ref, name, qty, buy, sell, minimum)
        except StockError:
            continue
    with get_conn() as conn:
        rows = {r["reference"]: r["id"] for r in conn.execute("SELECT id, reference FROM products")}
    for ref, move_type, qty, note in [
        ("SUC-001", MOVE_OUT, 15, "Vente comptoir"),
        ("HUI-001", MOVE_OUT, 6, "Vente comptoir"),
        ("THE-001", MOVE_IN, 12, "Livraison fournisseur"),
        ("SAV-001", MOVE_OUT, 10, "Commande client"),
    ]:
        if ref in rows:
            try:
                add_movement(rows[ref], move_type, qty, note)
            except StockError:
                pass


# --------------------------------------------------------------------------- #
# Aides d'interface
# --------------------------------------------------------------------------- #
CSS = """
<style>
.block-container { padding-top: 2rem; max-width: 1200px; }
[data-testid="stMetric"] {
    background: rgba(128, 128, 128, 0.08);
    border: 1px solid rgba(128, 128, 128, 0.22);
    border-radius: 12px;
    padding: 14px 16px;
}
[data-testid="stMetricValue"] { font-size: 1.6rem; }
div[data-testid="stForm"] { border-radius: 12px; }
@media (max-width: 640px) {
    .block-container { padding-left: 1rem; padding-right: 1rem; padding-top: 1rem; }
    [data-testid="stMetricValue"] { font-size: 1.3rem; }
}
</style>
"""


def flash(message: str, kind: str = "success") -> None:
    """Mémorise un message à afficher après le prochain rechargement de la page."""
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        kind, message = item
        {"success": st.success, "warning": st.warning, "error": st.error}.get(kind, st.info)(message)


def run_action(func, *args):
    """Exécute une opération et affiche l'erreur éventuelle. Retourne (ok, résultat)."""
    try:
        return True, func(*args)
    except StockError as exc:
        st.error(str(exc))
    except sqlite3.Error as exc:
        st.error(f"Erreur de base de données : {exc}")
    return False, None


def show_table(df: pd.DataFrame, column_config=None) -> None:
    try:
        st.dataframe(df, width="stretch", hide_index=True, column_config=column_config)
    except Exception:  # anciennes versions de Streamlit
        st.dataframe(df, use_container_width=True, hide_index=True, column_config=column_config)


def inventory_column_config() -> dict:
    money = st.column_config.NumberColumn(format="%.2f")
    return {
        INVENTORY_COLUMNS["purchase_price"]: money,
        INVENTORY_COLUMNS["selling_price"]: money,
        INVENTORY_COLUMNS["stock_value"]: money,
        INVENTORY_COLUMNS["profit"]: money,
    }


def product_labels(df: pd.DataFrame) -> dict:
    return {int(r.id): f"{r.reference} — {r.name}" for r in df.itertuples()}


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
def page_dashboard() -> None:
    st.title("📊 Tableau de bord")
    df = fetch_products()

    if df.empty:
        st.info("Bienvenue ! Votre stock est vide. Ajoutez votre premier produit "
                "dans la page « Produits », ou chargez des données d'exemple.")
        if st.button("Charger des données d'exemple"):
            ok, _ = run_action(seed_demo)
            if ok:
                flash("Données d'exemple chargées.")
                st.rerun()
        return

    s = inventory_summary(df)

    c1, c2, c3 = st.columns(3)
    c1.metric("Produits référencés", fmt_int(s["products"]))
    c2.metric("Unités en stock", fmt_int(s["units"]))
    c3.metric("Alertes de stock", fmt_int(s["low"] + s["out"]))

    c4, c5, c6 = st.columns(3)
    c4.metric("Valeur du stock (achat)", fmt_money(s["stock_value"]))
    c5.metric("Valeur de vente potentielle", fmt_money(s["sale_value"]))
    c6.metric("Bénéfice estimé", fmt_money(s["profit"]))
    st.caption(f"Marge estimée sur le stock actuel : {s['margin']:.1f} %")

    st.subheader("⚠️ Alertes de stock")
    alerts = df[df["status"] != STATUS_OK]
    if alerts.empty:
        st.success("Aucune alerte : tous les produits sont au-dessus de leur seuil.")
    else:
        if s["out"]:
            st.error(f"{s['out']} produit(s) en rupture de stock.")
        if s["low"]:
            st.warning(f"{s['low']} produit(s) sous le seuil d'alerte.")
        view = alerts.sort_values("quantity")[["reference", "name", "quantity", "min_stock", "status"]]
        view = view.rename(columns={
            "reference": "Référence", "name": "Produit", "quantity": "Quantité",
            "min_stock": "Seuil d'alerte", "status": "Statut",
        })
        view["Statut"] = view["Statut"].map(STATUS_ICONS)
        show_table(view)

    if df["stock_value"].max() > 0:
        st.subheader("💰 Top 10 : valeur du stock")
        top = df.nlargest(10, "stock_value")[["reference", "stock_value"]]
        top = top.set_index("reference").rename(columns={"stock_value": f"Valeur du stock ({CURRENCY})"})
        st.bar_chart(top)

    st.subheader("🕑 Derniers mouvements")
    moves = fetch_movements().head(8)
    if moves.empty:
        st.caption("Aucun mouvement enregistré.")
    else:
        show_table(movements_frame(moves))


def tab_inventory() -> None:
    df = fetch_products()
    if df.empty:
        st.info("Aucun produit pour le moment. Utilisez l'onglet « Ajouter » pour créer le premier.")
        return

    c1, c2, c3 = st.columns([2, 1, 1])
    query = c1.text_input("🔍 Rechercher", placeholder="Nom ou référence…").strip()
    status = c2.selectbox("Statut", ["Tous", STATUS_OK, STATUS_LOW, STATUS_OUT])
    sorts = {
        "Nom (A → Z)": ("name", True),
        "Référence (A → Z)": ("reference", True),
        "Quantité (croissante)": ("quantity", True),
        "Quantité (décroissante)": ("quantity", False),
        "Valeur du stock (décroissante)": ("stock_value", False),
    }
    sort_label = c3.selectbox("Trier par", list(sorts))

    view = df
    if query:
        mask = (view["name"].str.contains(query, case=False, regex=False)
                | view["reference"].str.contains(query, case=False, regex=False))
        view = view[mask]
    if status != "Tous":
        view = view[view["status"] == status]
    column, ascending = sorts[sort_label]
    if column in ("name", "reference"):
        view = view.sort_values(column, ascending=ascending, key=lambda s: s.str.lower())
    else:
        view = view.sort_values(column, ascending=ascending)

    s = inventory_summary(view)
    st.caption(
        f"{len(view)} produit(s) affiché(s) sur {len(df)} · "
        f"Valeur du stock : {fmt_money(s['stock_value'])} · "
        f"Bénéfice estimé : {fmt_money(s['profit'])}"
    )

    if view.empty:
        st.warning("Aucun produit ne correspond à votre recherche.")
    else:
        shown = inventory_frame(view)
        shown["Statut"] = shown["Statut"].map(STATUS_ICONS)
        show_table(shown, inventory_column_config())

    st.download_button(
        "⬇️ Exporter tout l'inventaire (CSV)",
        data=to_csv_bytes(inventory_frame(df)),
        file_name=f"inventaire_stockflow_{datetime.now():%Y-%m-%d}.csv",
        mime="text/csv",
    )


def tab_add() -> None:
    v = st.session_state.get("add_v", 0)
    with st.form(f"add_form_{v}"):
        c1, c2 = st.columns(2)
        reference = c1.text_input("Référence *", key=f"add_ref_{v}", max_chars=50,
                                  placeholder="ex. HUI-001")
        name = c2.text_input("Nom du produit *", key=f"add_name_{v}", max_chars=120,
                             placeholder="ex. Huile d'olive 1 L")
        c3, c4, c5, c6 = st.columns(4)
        quantity = c3.number_input("Quantité initiale", min_value=0, max_value=MAX_QTY,
                                   value=0, step=1, key=f"add_qty_{v}")
        purchase = c4.number_input(f"Prix d'achat ({CURRENCY})", min_value=0.0,
                                   max_value=MAX_PRICE, value=0.0, step=1.0,
                                   format="%.2f", key=f"add_buy_{v}")
        selling = c5.number_input(f"Prix de vente ({CURRENCY})", min_value=0.0,
                                  max_value=MAX_PRICE, value=0.0, step=1.0,
                                  format="%.2f", key=f"add_sell_{v}")
        min_stock = c6.number_input("Seuil d'alerte", min_value=0, max_value=MAX_QTY,
                                    value=DEFAULT_MIN_STOCK, step=1, key=f"add_min_{v}")
        submitted = st.form_submit_button("➕ Ajouter le produit", type="primary")

    if submitted:
        ok, _ = run_action(add_product, reference, name, quantity, purchase, selling, min_stock)
        if ok:
            message = f"Produit « {name.strip()} » ajouté."
            if selling < purchase:
                message += " ⚠️ Attention : le prix de vente est inférieur au prix d'achat."
            flash(message)
            st.session_state["add_v"] = v + 1
            st.rerun()


def tab_edit_delete() -> None:
    df = fetch_products()
    if df.empty:
        st.info("Aucun produit à modifier ou supprimer.")
        return

    labels = product_labels(df)
    pid = st.selectbox("Choisir un produit", list(labels), format_func=labels.get)
    row = df[df["id"] == pid].iloc[0]

    st.subheader("✏️ Modifier")
    st.caption(f"Quantité en stock : **{int(row['quantity'])}**. "
               "Pour la changer, enregistrez une entrée ou une sortie dans « Mouvements de stock ».")
    with st.form(f"edit_form_{pid}"):
        c1, c2 = st.columns(2)
        reference = c1.text_input("Référence *", value=str(row["reference"]),
                                  key=f"e_ref_{pid}", max_chars=50)
        name = c2.text_input("Nom du produit *", value=str(row["name"]),
                             key=f"e_name_{pid}", max_chars=120)
        c3, c4, c5 = st.columns(3)
        purchase = c3.number_input(f"Prix d'achat ({CURRENCY})", min_value=0.0,
                                   max_value=MAX_PRICE, value=float(row["purchase_price"]),
                                   step=1.0, format="%.2f", key=f"e_buy_{pid}")
        selling = c4.number_input(f"Prix de vente ({CURRENCY})", min_value=0.0,
                                  max_value=MAX_PRICE, value=float(row["selling_price"]),
                                  step=1.0, format="%.2f", key=f"e_sell_{pid}")
        min_stock = c5.number_input("Seuil d'alerte", min_value=0, max_value=MAX_QTY,
                                    value=int(row["min_stock"]), step=1, key=f"e_min_{pid}")
        submitted = st.form_submit_button("💾 Enregistrer les modifications", type="primary")

    if submitted:
        ok, _ = run_action(update_product, pid, reference, name, purchase, selling, min_stock)
        if ok:
            message = "Produit modifié."
            if selling < purchase:
                message += " ⚠️ Attention : le prix de vente est inférieur au prix d'achat."
            flash(message)
            st.rerun()

    st.divider()
    st.subheader("🗑️ Supprimer")
    st.caption("La suppression est définitive. L'historique des mouvements du produit est conservé.")
    confirm = st.checkbox("Je confirme vouloir supprimer ce produit", key=f"del_ok_{pid}")
    if st.button("Supprimer le produit", disabled=not confirm, key=f"del_btn_{pid}"):
        ok, _ = run_action(delete_product, pid)
        if ok:
            flash(f"Produit « {row['name']} » supprimé.")
            st.rerun()


def page_products() -> None:
    st.title("📦 Produits")
    tab1, tab2, tab3 = st.tabs(["Inventaire", "Ajouter", "Modifier / Supprimer"])
    with tab1:
        tab_inventory()
    with tab2:
        tab_add()
    with tab3:
        tab_edit_delete()


def movements_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["created_at", "reference", "name", "type", "quantity", "note"]].copy()
    out["type"] = out["type"].map({MOVE_IN: "⬆️ Entrée", MOVE_OUT: "⬇️ Sortie"})
    return out.rename(columns={
        "created_at": "Date", "reference": "Référence", "name": "Produit",
        "type": "Type", "quantity": "Quantité", "note": "Note",
    })


def tab_new_movement() -> None:
    df = fetch_products()
    if df.empty:
        st.info("Ajoutez d'abord un produit pour pouvoir enregistrer des mouvements.")
        return

    labels = product_labels(df)
    pid = st.selectbox("Produit", list(labels), format_func=labels.get)
    row = df[df["id"] == pid].iloc[0]
    st.caption(f"Stock actuel : **{int(row['quantity'])}** unité(s) · "
               f"Seuil d'alerte : {int(row['min_stock'])}")

    v = st.session_state.get("mv_v", 0)
    with st.form(f"mv_form_{v}"):
        move_type = st.radio("Type de mouvement", [MOVE_IN, MOVE_OUT], horizontal=True,
                             key=f"mv_type_{v}")
        quantity = st.number_input("Quantité", min_value=1, max_value=MAX_QTY, value=1,
                                   step=1, key=f"mv_qty_{v}")
        note = st.text_input("Note (facultatif)", max_chars=200, key=f"mv_note_{v}",
                             placeholder="ex. Livraison fournisseur, vente comptoir…")
        submitted = st.form_submit_button("✅ Enregistrer le mouvement", type="primary")

    if submitted:
        ok, new_qty = run_action(add_movement, pid, move_type, quantity, note)
        if ok:
            message = f"{move_type} enregistrée : {int(quantity)} unité(s). Nouveau stock : {new_qty}."
            kind = "success"
            if new_qty <= 0:
                message += " ⚠️ Ce produit est maintenant en rupture de stock."
                kind = "warning"
            elif new_qty <= int(row["min_stock"]):
                message += " ⚠️ Le stock est passé sous le seuil d'alerte."
                kind = "warning"
            flash(message, kind)
            st.session_state["mv_v"] = v + 1
            st.rerun()


def tab_history() -> None:
    moves = fetch_movements()
    if moves.empty:
        st.info("Aucun mouvement enregistré pour le moment.")
        return

    c1, c2, c3 = st.columns(3)
    product_options = ["Tous les produits"] + sorted(
        {f"{r.reference} — {r.name}" for r in moves.itertuples()}, key=str.lower
    )
    product = c1.selectbox("Produit", product_options)
    kind = c2.selectbox("Type", ["Tous", MOVE_IN, MOVE_OUT])
    period = c3.selectbox("Période", ["Tout", "Aujourd'hui", "7 derniers jours", "30 derniers jours"])

    view = moves
    if product != "Tous les produits":
        labels = view["reference"] + " — " + view["name"]
        view = view[labels == product]
    if kind != "Tous":
        view = view[view["type"] == kind]
    now = datetime.now()
    cutoffs = {
        "Aujourd'hui": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "7 derniers jours": now - timedelta(days=7),
        "30 derniers jours": now - timedelta(days=30),
    }
    if period in cutoffs:
        view = view[view["created_at"] >= cutoffs[period].strftime("%Y-%m-%d %H:%M:%S")]

    total_in = int(view.loc[view["type"] == MOVE_IN, "quantity"].sum())
    total_out = int(view.loc[view["type"] == MOVE_OUT, "quantity"].sum())
    m1, m2, m3 = st.columns(3)
    m1.metric("Mouvements", fmt_int(len(view)))
    m2.metric("Unités entrées", fmt_int(total_in))
    m3.metric("Unités sorties", fmt_int(total_out))

    if view.empty:
        st.warning("Aucun mouvement ne correspond à ces filtres.")
        return
    show_table(movements_frame(view))
    plain = movements_frame(view)
    plain["Type"] = view["type"].values
    st.download_button(
        "⬇️ Exporter l'historique (CSV)",
        data=to_csv_bytes(plain),
        file_name=f"mouvements_stockflow_{datetime.now():%Y-%m-%d}.csv",
        mime="text/csv",
    )


def page_movements() -> None:
    st.title("🔄 Mouvements de stock")
    tab1, tab2 = st.tabs(["Nouveau mouvement", "Historique"])
    with tab1:
        tab_new_movement()
    with tab2:
        tab_history()


# --------------------------------------------------------------------------- #
# Point d'entrée
# --------------------------------------------------------------------------- #
PAGES = {
    "📊 Tableau de bord": page_dashboard,
    "📦 Produits": page_products,
    "🔄 Mouvements de stock": page_movements,
}


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="📦", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    try:
        init_db()
    except (sqlite3.Error, OSError) as exc:
        st.error(f"Impossible d'ouvrir la base de données ({DB_PATH}) : {exc}")
        st.stop()

    st.sidebar.markdown(f"## 📦 {APP_NAME}")
    st.sidebar.caption("Gestion de stock pour petites entreprises")
    page = st.sidebar.radio("Navigation", list(PAGES), label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption(f"Base de données : {DB_PATH.name}")

    show_flash()
    try:
        PAGES[page]()
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        st.error(f"Erreur de base de données : {exc}")


if __name__ == "__main__":
    main()
