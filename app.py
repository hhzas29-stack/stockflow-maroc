# -*- coding: utf-8 -*-
"""StockFlow Maroc — gestion de stock, fournisseurs, mouvements et analyses."""
from __future__ import annotations
import io, os, sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

APP_NAME = "StockFlow Maroc"
CURRENCY = "DH"
DB_PATH = Path(os.environ.get("STOCKFLOW_DB", str(Path(__file__).resolve().with_name("stockflow.db"))))
MOVE_IN, MOVE_OUT = "Entrée", "Sortie"
LOW_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 phone TEXT DEFAULT '', email TEXT DEFAULT '', notes TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 reference TEXT NOT NULL UNIQUE COLLATE NOCASE,
 name TEXT NOT NULL,
 quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
 purchase_price REAL NOT NULL DEFAULT 0 CHECK(purchase_price >= 0),
 selling_price REAL NOT NULL DEFAULT 0 CHECK(selling_price >= 0),
 min_stock INTEGER NOT NULL DEFAULT 5 CHECK(min_stock >= 0),
 supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS movements (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
 product_ref TEXT NOT NULL, product_name TEXT NOT NULL,
 type TEXT NOT NULL CHECK(type IN ('Entrée','Sortie')),
 quantity INTEGER NOT NULL CHECK(quantity > 0),
 unit_price REAL NOT NULL DEFAULT 0,
 total_value REAL NOT NULL DEFAULT 0,
 note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_movements_date ON movements(created_at);
CREATE INDEX IF NOT EXISTS idx_movements_product ON movements(product_id);
"""

@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

def now_str(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    with get_conn() as c:
        c.executescript(SCHEMA)
        # Migration sans supprimer les données d'une ancienne version.
        pcols = {r[1] for r in c.execute("PRAGMA table_info(products)")}
        if "supplier_id" not in pcols:
            c.execute("ALTER TABLE products ADD COLUMN supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL")
        mcols = {r[1] for r in c.execute("PRAGMA table_info(movements)")}
        if "unit_price" not in mcols: c.execute("ALTER TABLE movements ADD COLUMN unit_price REAL NOT NULL DEFAULT 0")
        if "total_value" not in mcols: c.execute("ALTER TABLE movements ADD COLUMN total_value REAL NOT NULL DEFAULT 0")

def get_products():
    with get_conn() as c:
        df = pd.read_sql_query("""SELECT p.*, s.name AS supplier_name FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id ORDER BY p.name COLLATE NOCASE""", c)
    if df.empty:
        for col in ["stock_value","sale_value","profit","status"]: df[col] = pd.Series(dtype="object")
        return df
    df["stock_value"] = (df.quantity * df.purchase_price).round(2)
    df["sale_value"] = (df.quantity * df.selling_price).round(2)
    df["profit"] = (df.sale_value - df.stock_value).round(2)
    df["status"] = "OK"
    df.loc[df.quantity <= df.min_stock, "status"] = "Stock faible"
    df.loc[df.quantity <= 0, "status"] = "Rupture"
    return df

def get_movements():
    with get_conn() as c:
        return pd.read_sql_query("""SELECT m.*, COALESCE(p.reference,m.product_ref) AS reference,
          COALESCE(p.name,m.product_name) AS name FROM movements m
          LEFT JOIN products p ON p.id=m.product_id ORDER BY m.created_at DESC,m.id DESC""", c)

def get_suppliers():
    with get_conn() as c:
        return pd.read_sql_query("""SELECT s.*, COUNT(p.id) AS products_count FROM suppliers s
          LEFT JOIN products p ON p.supplier_id=s.id GROUP BY s.id ORDER BY s.name COLLATE NOCASE""", c)

def add_supplier(name, phone, email, notes):
    name = name.strip()
    if not name: raise ValueError("Le nom du fournisseur est obligatoire.")
    with get_conn() as c:
        c.execute("INSERT INTO suppliers(name,phone,email,notes,created_at) VALUES(?,?,?,?,?)", (name, phone.strip(), email.strip(), notes.strip(), now_str()))

def update_supplier(sid, name, phone, email, notes):
    if not name.strip(): raise ValueError("Le nom du fournisseur est obligatoire.")
    with get_conn() as c:
        c.execute("UPDATE suppliers SET name=?,phone=?,email=?,notes=? WHERE id=?", (name.strip(), phone.strip(), email.strip(), notes.strip(), int(sid)))

def delete_supplier(sid):
    with get_conn() as c: c.execute("DELETE FROM suppliers WHERE id=?", (int(sid),))

def add_product(ref, name, qty, buy, sell, min_stock, supplier_id):
    ref, name = ref.strip(), name.strip()
    if not ref or not name: raise ValueError("La référence et le nom du produit sont obligatoires.")
    if int(qty) < 0 or int(min_stock) < 0 or float(buy) < 0 or float(sell) < 0: raise ValueError("Quantités et prix doivent être positifs ou nuls.")
    with get_conn() as c:
        cur = c.execute("INSERT INTO products(reference,name,quantity,purchase_price,selling_price,min_stock,supplier_id,created_at) VALUES(?,?,?,?,?,?,?,?)", (ref,name,int(qty),float(buy),float(sell),int(min_stock),supplier_id,now_str()))
        pid = cur.lastrowid
        if int(qty) > 0:
            c.execute("INSERT INTO movements(product_id,product_ref,product_name,type,quantity,unit_price,total_value,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (pid,ref,name,MOVE_IN,int(qty),float(buy),int(qty)*float(buy),"Stock initial",now_str()))

def update_product(pid, ref, name, buy, sell, min_stock, supplier_id):
    if not ref.strip() or not name.strip(): raise ValueError("La référence et le nom sont obligatoires.")
    with get_conn() as c:
        c.execute("UPDATE products SET reference=?,name=?,purchase_price=?,selling_price=?,min_stock=?,supplier_id=? WHERE id=?", (ref.strip(),name.strip(),float(buy),float(sell),int(min_stock),supplier_id,int(pid)))

def delete_product(pid):
    with get_conn() as c: c.execute("DELETE FROM products WHERE id=?", (int(pid),))

def add_movement(pid, typ, qty, note="", unit_price=None):
    qty = int(qty)
    if qty < 1: raise ValueError("La quantité doit être au moins 1.")
    if typ not in (MOVE_IN, MOVE_OUT): raise ValueError("Type de mouvement invalide.")
    with get_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        p = c.execute("SELECT * FROM products WHERE id=?", (int(pid),)).fetchone()
        if not p: raise ValueError("Produit introuvable.")
        newqty = p["quantity"] + qty if typ == MOVE_IN else p["quantity"] - qty
        if newqty < 0: raise ValueError(f"Stock insuffisant : {p['quantity']} unité(s) disponible(s).")
        price = float(unit_price if unit_price is not None else (p["selling_price"] if typ == MOVE_OUT else p["purchase_price"]))
        if price < 0: raise ValueError("Le prix ne peut pas être négatif.")
        c.execute("UPDATE products SET quantity=? WHERE id=?", (newqty,int(pid)))
        c.execute("INSERT INTO movements(product_id,product_ref,product_name,type,quantity,unit_price,total_value,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (int(pid),p["reference"],p["name"],typ,qty,price,qty*price,(note or "").strip()[:250],now_str()))
    return newqty

def money(x): return f"{float(x):,.2f} DH".replace(",", " ")
def intfmt(x): return f"{int(x):,}".replace(",", " ")
def csv_bytes(df): return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
def excel_bytes(sheets):
    b=io.BytesIO()
    with pd.ExcelWriter(b,engine="openpyxl") as w:
        for name,df in sheets.items():
            df.to_excel(w,index=False,sheet_name=name[:31]); ws=w.sheets[name[:31]]; ws.freeze_panes="A2"; ws.auto_filter.ref=ws.dimensions
            for cells in ws.columns:
                ws.column_dimensions[cells[0].column_letter].width=min(max(max(len(str(x.value or "")) for x in cells)+2,12),40)
    return b.getvalue()
def supplier_options(suppliers):
    opts={int(r.id):r.name for r in suppliers.itertuples()}
    return opts

def show_df(df):
    try: st.dataframe(df, use_container_width=True, hide_index=True)
    except TypeError: st.dataframe(df, width="stretch", hide_index=True)

CSS="""
<style>
.block-container{padding-top:1.3rem;max-width:1400px}
[data-testid="stMetric"]{background:linear-gradient(140deg,rgba(28,116,176,.12),rgba(128,128,128,.035));border:1px solid rgba(128,128,128,.22);border-radius:15px;padding:14px 16px}
[data-testid="stMetricValue"]{font-size:1.55rem;font-weight:700}
.hero{padding:1.2rem 1.4rem;border:1px solid rgba(128,128,128,.22);border-radius:18px;margin-bottom:1rem;background:linear-gradient(120deg,rgba(28,116,176,.15),rgba(128,128,128,.03))}
@media(max-width:640px){.block-container{padding-left:.8rem;padding-right:.8rem}[data-testid="stMetricValue"]{font-size:1.15rem}}
</style>"""

def dashboard():
    p=get_products(); m=get_movements()
    st.markdown('<div class="hero"><h2>📊 StockFlow Maroc</h2><p>Vue globale de votre stock, des ventes estimées et des alertes — montants en dirhams marocains (DH).</p></div>',unsafe_allow_html=True)
    if p.empty:
        st.info("Votre inventaire est vide. Ajoutez des produits dans la section Produits.")
        return
    total_cost=float(p.stock_value.sum()); sale_value=float(p.sale_value.sum()); potential=float(p.profit.sum())
    low=int((p.status=="Stock faible").sum()); out=int((p.status=="Rupture").sum())
    a=st.columns(4)
    a[0].metric("📦 Références",intfmt(len(p))); a[1].metric("🔢 Unités en stock",intfmt(p.quantity.sum()))
    a[2].metric("⚠️ Alertes",intfmt(low+out)); a[3].metric("💰 Valeur d'achat",money(total_cost))
    b=st.columns(3); b[0].metric("🏷️ Vente potentielle",money(sale_value)); b[1].metric("📈 Marge brute potentielle",money(potential)); b[2].metric("Marge estimée",f"{(potential/sale_value*100 if sale_value else 0):.1f} %")
    left,right=st.columns(2)
    with left:
        st.subheader("🏆 Top 5 — Valeur immobilisée")
        top=p.nlargest(5,"stock_value").sort_values("stock_value")
        if not top.empty:
            fig=px.bar(top,x="stock_value",y="name",orientation="h",labels={"stock_value":"Valeur d'achat (DH)","name":"Produit"},text_auto=".2s")
            fig.update_layout(height=340,margin=dict(l=5,r=10,t=20,b=5),yaxis_title="",xaxis_title="Valeur (DH)")
            st.plotly_chart(fig,use_container_width=True)
    with right:
        st.subheader("📉 Valeur des sorties / bénéfice brut estimé")
        if not m.empty:
            sales=m[m.type==MOVE_OUT].copy()
            if not sales.empty:
                sales["date"]=pd.to_datetime(sales.created_at,errors="coerce").dt.date
                daily=sales.groupby("date",as_index=False).agg(Ventes=("total_value","sum"),Quantites=("quantity","sum"))
                # Marge brute estimée par mouvement : prix de sortie enregistré moins coût d'achat actuel du produit.
                costs=p.set_index("id")["purchase_price"].to_dict()
                sales["gross_profit"]=[float(r.total_value)-float(r.quantity)*float(costs.get(r.product_id,0)) for r in sales.itertuples()]
                sales_daily=sales.groupby("date",as_index=False).agg(**{"Ventes (DH)":("total_value","sum"),"Bénéfice brut estimé (DH)":("gross_profit","sum")})
                sales_long=sales_daily.melt(id_vars="date",var_name="Indicateur",value_name="Montant (DH)")
                fig=px.line(sales_long,x="date",y="Montant (DH)",color="Indicateur",markers=True)
                fig.update_layout(height=340,margin=dict(l=5,r=10,t=20,b=5),xaxis_title="Date",yaxis_title="DH",legend_title="")
                st.plotly_chart(fig,use_container_width=True)
                st.caption("Le bénéfice est estimatif : il utilise le prix d'achat actuel du produit, pas nécessairement son coût historique au moment de la sortie.")
            else: st.info("Les sorties enregistrées apparaîtront ici.")
        else: st.info("Enregistrez des mouvements pour alimenter le graphique.")
    st.subheader("🚨 Produits à surveiller")
    alerts=p[p.status!="OK"].copy()
    if alerts.empty: st.success("Aucun produit en alerte de stock.")
    else:
        alerts["Statut"]=alerts.status.map({"Rupture":"🔴 Rupture","Stock faible":"🟠 Stock faible"})
        show_df(alerts[["reference","name","quantity","min_stock","supplier_name","Statut"]].rename(columns={"reference":"Référence","name":"Produit","quantity":"Stock","min_stock":"Seuil","supplier_name":"Fournisseur"}))
    st.subheader(f"🕰️ Stock dormant — aucune activité depuis {LOW_DAYS} jours")
    if not p.empty:
        last={}
        if not m.empty:
            for r in m.itertuples():
                if r.product_id is not None and r.product_id not in last: last[r.product_id]=r.created_at
        p["last_movement"] = p.apply(lambda r:last.get(r.id, r.created_at),axis=1)
        p["last_dt"] = pd.to_datetime(p.last_movement,errors="coerce")
        dormant=p[(p.quantity>0)&(p.last_dt < pd.Timestamp(datetime.now()-timedelta(days=LOW_DAYS)))].copy()
        if dormant.empty: st.success("Aucun stock dormant détecté selon le seuil actuel.")
        else: show_df(dormant[["reference","name","quantity","last_movement","supplier_name"]].rename(columns={"reference":"Référence","name":"Produit","quantity":"Stock","last_movement":"Dernier mouvement","supplier_name":"Fournisseur"}))
    st.subheader("🕑 Derniers mouvements")
    if m.empty: st.caption("Aucun mouvement enregistré.")
    else: show_df(m.head(8)[["created_at","reference","name","type","quantity","unit_price","total_value","note"]].rename(columns={"created_at":"Date","reference":"Référence","name":"Produit","type":"Type","quantity":"Qté","unit_price":"Prix unitaire (DH)","total_value":"Total (DH)","note":"Note"}))

def products_page():
    st.title("📦 Gestion des produits")
    p=get_products(); s=get_suppliers(); opts=supplier_options(s); supplier_names={v:k for k,v in opts.items()}
    t1,t2,t3=st.tabs(["Inventaire","Ajouter un produit","Modifier / supprimer"])
    with t1:
        if p.empty: st.info("Aucun produit pour le moment.")
        else:
            q=st.text_input("Rechercher par référence ou nom",key="prod_search")
            view=p.copy()
            if q: view=view[view.reference.str.contains(q,case=False,na=False)|view.name.str.contains(q,case=False,na=False)]
            show_df(view[["reference","name","quantity","purchase_price","selling_price","min_stock","supplier_name","stock_value","profit","status"]].rename(columns={"reference":"Référence","name":"Produit","quantity":"Stock","purchase_price":"Prix achat (DH)","selling_price":"Prix vente (DH)","min_stock":"Seuil","supplier_name":"Fournisseur","stock_value":"Valeur stock (DH)","profit":"Bénéfice potentiel (DH)","status":"Statut"}))
            st.download_button("Télécharger inventaire CSV",csv_bytes(view),f"inventaire_stockflow_{datetime.now():%Y%m%d}.csv","text/csv")
    with t2:
        with st.form("add_product_form",clear_on_submit=True):
            c1,c2=st.columns(2); ref=c1.text_input("Référence *"); name=c2.text_input("Nom du produit *")
            c3,c4,c5=st.columns(3); qty=c3.number_input("Stock initial",0,1_000_000,0); buy=c4.number_input("Prix d'achat (DH)",0.0,10_000_000.0,0.0,step=1.0); sell=c5.number_input("Prix de vente (DH)",0.0,10_000_000.0,0.0,step=1.0)
            c6,c7=st.columns(2); minimum=c6.number_input("Seuil d'alerte",0,1_000_000,5)
            sn=c7.selectbox("Fournisseur",["— Aucun —"]+list(supplier_names.keys()))
            submit=st.form_submit_button("Ajouter le produit",type="primary")
        if submit:
            try:
                add_product(ref,name,qty,buy,sell,minimum,supplier_names.get(sn)); st.success("Produit ajouté."); st.rerun()
            except (ValueError,sqlite3.Error) as e: st.error(f"Impossible d'ajouter le produit : {e}")
    with t3:
        if p.empty: st.info("Aucun produit à modifier.")
        else:
            labels={int(r.id):f"{r.reference} — {r.name}" for r in p.itertuples()}; pid=st.selectbox("Choisir un produit",list(labels),format_func=labels.get); r=p[p.id==pid].iloc[0]
            with st.form(f"edit_product_{pid}"):
                c1,c2=st.columns(2); ref=c1.text_input("Référence",str(r.reference)); name=c2.text_input("Nom",str(r["name"]))
                c3,c4,c5=st.columns(3); buy=c3.number_input("Prix d'achat",0.0,10_000_000.0,float(r.purchase_price),step=1.0); sell=c4.number_input("Prix de vente",0.0,10_000_000.0,float(r.selling_price),step=1.0); minimum=c5.number_input("Seuil d'alerte",0,1_000_000,int(r.min_stock))
                cur_supplier=opts.get(int(r.supplier_id)) if pd.notna(r.supplier_id) else None
                sn=st.selectbox("Fournisseur",["— Aucun —"]+list(supplier_names.keys()),index=((["— Aucun —"]+list(supplier_names.keys())).index(cur_supplier) if cur_supplier in supplier_names else 0))
                save=st.form_submit_button("Enregistrer les modifications",type="primary")
            if save:
                try: update_product(pid,ref,name,buy,sell,minimum,supplier_names.get(sn)); st.success("Produit modifié."); st.rerun()
                except (ValueError,sqlite3.Error) as e: st.error(f"Modification impossible : {e}")
            st.divider(); confirm=st.checkbox("Je confirme la suppression définitive",key=f"confirm_del_{pid}")
            if st.button("Supprimer ce produit",disabled=not confirm):
                try: delete_product(pid); st.success("Produit supprimé."); st.rerun()
                except sqlite3.Error as e: st.error(str(e))

def movements_page():
    st.title("🔄 Mouvements de stock")
    p=get_products(); m=get_movements(); t1,t2=st.tabs(["Nouvelle entrée / sortie","Historique"])
    with t1:
        if p.empty: st.info("Ajoutez d'abord un produit.")
        else:
            labels={int(r.id):f"{r.reference} — {r['name']} (stock : {r.quantity})" for _,r in p.iterrows()}
            pid=st.selectbox("Produit",list(labels),format_func=labels.get,key="movement_product")
            r=p[p.id==pid].iloc[0]
            with st.form("movement_form",clear_on_submit=True):
                typ=st.radio("Type",[MOVE_IN,MOVE_OUT],horizontal=True); qty=st.number_input("Quantité",1,1_000_000,1)
                default_price=float(r.purchase_price if typ==MOVE_IN else r.selling_price)
                price=st.number_input("Prix unitaire (DH)",0.0,10_000_000.0,default_price,step=1.0,help="Pour une sortie, utilisez le prix de vente réel si vous le connaissez.")
                note=st.text_input("Note (livraison, vente, retour...)"); submit=st.form_submit_button("Enregistrer le mouvement",type="primary")
            if submit:
                try:
                    newqty=add_movement(pid,typ,qty,note,price); st.success(f"Mouvement enregistré. Nouveau stock : {newqty} unité(s)."); st.rerun()
                except (ValueError,sqlite3.Error) as e: st.error(str(e))
    with t2:
        m=get_movements()
        if m.empty: st.info("Aucun mouvement enregistré.")
        else:
            c1,c2,c3=st.columns(3); product=c1.selectbox("Produit",["Tous"]+sorted((m.reference+" — "+m.name).unique().tolist())); typ=c2.selectbox("Type",["Tous",MOVE_IN,MOVE_OUT]); period=c3.selectbox("Période",["Tout","Aujourd'hui","7 jours","30 jours"])
            v=m.copy()
            if product!="Tous": v=v[(v.reference+" — "+v.name)==product]
            if typ!="Tous": v=v[v.type==typ]
            now=datetime.now(); cutoff={"Aujourd'hui":now.replace(hour=0,minute=0,second=0,microsecond=0),"7 jours":now-timedelta(days=7),"30 jours":now-timedelta(days=30)}.get(period)
            if cutoff: v=v[pd.to_datetime(v.created_at)>=cutoff]
            show_df(v[["created_at","reference","name","type","quantity","unit_price","total_value","note"]].rename(columns={"created_at":"Date","reference":"Référence","name":"Produit","type":"Type","quantity":"Quantité","unit_price":"Prix unitaire (DH)","total_value":"Total (DH)","note":"Note"}))
            st.download_button("Exporter l'historique CSV",csv_bytes(v),f"mouvements_{datetime.now():%Y%m%d}.csv","text/csv")

def suppliers_page():
    st.title("🤝 Fournisseurs")
    s=get_suppliers(); p=get_products(); t1,t2,t3=st.tabs(["Liste des fournisseurs","Ajouter","Modifier / supprimer"])
    with t1:
        if s.empty: st.info("Aucun fournisseur enregistré.")
        else: show_df(s[["name","phone","email","products_count","notes"]].rename(columns={"name":"Fournisseur","phone":"Téléphone","email":"Email","products_count":"Produits liés","notes":"Notes"}))
    with t2:
        with st.form("add_supplier_form",clear_on_submit=True):
            name=st.text_input("Nom du fournisseur *"); c1,c2=st.columns(2); phone=c1.text_input("Téléphone"); email=c2.text_input("Email"); notes=st.text_area("Notes / produits proposés"); submit=st.form_submit_button("Ajouter fournisseur",type="primary")
        if submit:
            try: add_supplier(name,phone,email,notes); st.success("Fournisseur ajouté."); st.rerun()
            except (ValueError,sqlite3.Error) as e: st.error(f"Ajout impossible : {e}")
    with t3:
        if s.empty: st.info("Aucun fournisseur à modifier.")
        else:
            labels={int(r.id):r.name for r in s.itertuples()}; sid=st.selectbox("Fournisseur",list(labels),format_func=labels.get); r=s[s.id==sid].iloc[0]
            with st.form(f"edit_supplier_{sid}"):
                name=st.text_input("Nom",str(r["name"])); c1,c2=st.columns(2); phone=c1.text_input("Téléphone",str(r.phone or "")); email=c2.text_input("Email",str(r.email or "")); notes=st.text_area("Notes",str(r.notes or "")); save=st.form_submit_button("Enregistrer",type="primary")
            if save:
                try: update_supplier(sid,name,phone,email,notes); st.success("Fournisseur modifié."); st.rerun()
                except (ValueError,sqlite3.Error) as e: st.error(f"Modification impossible : {e}")
            st.caption("Si vous supprimez un fournisseur, les produits liés restent dans l'inventaire mais sans fournisseur.")
            confirm=st.checkbox("Confirmer la suppression",key=f"confirm_supplier_{sid}")
            if st.button("Supprimer le fournisseur",disabled=not confirm):
                try: delete_supplier(sid); st.success("Fournisseur supprimé."); st.rerun()
                except sqlite3.Error as e: st.error(str(e))

def backup_page():
    st.title("💾 Sauvegarde & données")
    p=get_products(); m=get_movements(); s=get_suppliers()
    inv=p.rename(columns={"reference":"Référence","name":"Produit","quantity":"Stock","purchase_price":"Prix achat (DH)","selling_price":"Prix vente (DH)","min_stock":"Seuil","supplier_name":"Fournisseur","stock_value":"Valeur stock (DH)","sale_value":"Vente potentielle (DH)","profit":"Bénéfice potentiel (DH)","status":"Statut"})
    hist=m.rename(columns={"created_at":"Date","reference":"Référence","name":"Produit","type":"Type","quantity":"Quantité","unit_price":"Prix unitaire (DH)","total_value":"Total (DH)","note":"Note"})
    sup=s.rename(columns={"name":"Fournisseur","phone":"Téléphone","email":"Email","notes":"Notes","products_count":"Produits liés","created_at":"Créé le"})
    st.write("Téléchargez une copie des produits, des mouvements et des fournisseurs.")
    st.download_button("Télécharger la sauvegarde Excel complète",excel_bytes({"Inventaire":inv,"Mouvements":hist,"Fournisseurs":sup}),f"stockflow_backup_{datetime.now():%Y%m%d_%H%M}.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",type="primary")
    st.download_button("Télécharger l'inventaire CSV",csv_bytes(inv),"stockflow_inventaire.csv","text/csv")
    st.download_button("Télécharger les mouvements CSV",csv_bytes(hist),"stockflow_mouvements.csv","text/csv")
    st.download_button("Télécharger les fournisseurs CSV",csv_bytes(sup),"stockflow_fournisseurs.csv","text/csv")
    st.divider(); st.subheader("⚠️ Persistance SQLite sur Streamlit Cloud")
    st.warning("SQLite enregistre les données dans un fichier local. Sur Streamlit Community Cloud, ce disque n'est pas garanti persistant : un redéploiement, un redémarrage ou une recréation de l'instance peut entraîner la perte des données. Les sauvegardes Excel protègent contre la perte, mais ne rendent pas la base persistante automatiquement.")
    st.markdown("**Pour une persistance fiable en production :** utilisez une base de données externe persistante (par exemple PostgreSQL managé via Supabase/Neon) et stockez les identifiants dans `st.secrets`. Le chemin `STOCKFLOW_DB` permet de choisir le fichier SQLite en local ou sur un serveur avec disque persistant, mais ne crée pas à lui seul un disque persistant sur Streamlit Cloud.")
    st.code('''# .streamlit/secrets.toml (exemple local seulement — ne pas publier ce fichier)\n# DATABASE_URL = "postgresql://..."\n\n# SQLite local / serveur avec disque persistant :\n# STOCKFLOW_DB=/chemin/persistant/stockflow.db''',language="toml")

def main():
    st.set_page_config(page_title=APP_NAME,page_icon="📦",layout="wide")
    st.markdown(CSS,unsafe_allow_html=True)
    try: init_db()
    except (sqlite3.Error,OSError) as e: st.error(f"Impossible d'ouvrir la base de données : {e}"); st.stop()
    st.sidebar.markdown(f"## 📦 {APP_NAME}"); st.sidebar.caption("Gestion de stock pour les petites entreprises marocaines")
    pages={"📊 Dashboard":dashboard,"📦 Produits":products_page,"🔄 Mouvements de stock":movements_page,"🤝 Fournisseurs":suppliers_page,"💾 Sauvegarde & données":backup_page}
    page=st.sidebar.radio("Navigation",list(pages),label_visibility="collapsed")
    st.sidebar.divider(); st.sidebar.caption(f"Base locale : {DB_PATH.name}")
    p=get_products(); m=get_movements(); s=get_suppliers()
    st.sidebar.download_button("⬇️ Sauvegarde Excel",excel_bytes({"Inventaire":p,"Mouvements":m,"Fournisseurs":s}),f"stockflow_backup_{datetime.now():%Y%m%d}.xlsx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",use_container_width=True)
    pages[page]()

if __name__ == "__main__": main()
