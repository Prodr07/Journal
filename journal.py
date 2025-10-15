import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
from datetime import datetime, date
import calendar
import re
from pathlib import Path

# =============================================================
# Trading Journal Pro — Supabase Beta (solo DB, sin CSV)
# - Auth: email + password (Supabase Auth)
# - Datos: tablas 'trades_raw' (entrada) y 'trades' (normalizada)
# - RLS: user_id = auth.uid() (Configurar en Supabase)
# - Secrets: st.secrets['SUPABASE_URL'], st.secrets['SUPABASE_ANON_KEY']
# =============================================================

st.set_page_config(page_title="Trading Journal Pro — Beta", layout="wide")

# -------------------------
# ⚙️ Conexión Supabase
# -------------------------
from supabase import create_client

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_ANON_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Faltan SUPABASE_URL y/o SUPABASE_ANON_KEY en st.secrets")
    st.stop()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------
# 🎨 Estilos
# -------------------------
CUSTOM_CSS = """
<style>
.main > div { padding-top: 1rem; }
.login-card { max-width: 420px; margin: 5vh auto; padding: 28px; border-radius: 18px; box-shadow: 0 8px 24px rgba(0,0,0,.08); background: white; }
.login-title { font-weight: 800; font-size: 1.6rem; margin-bottom: .5rem; background: linear-gradient(135deg,#667eea,#764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.subtle { color: #6b7280; font-size: .92rem; margin-bottom: .75rem; }
.brand { font-weight: 700; letter-spacing: .2px; }
.kpi-card { border-radius: 16px; padding: 14px 16px; background: #fafafa; border: 1px solid #eaeaea; }
.calendar { width: 100%; border-collapse: collapse; font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto; margin-bottom: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.06);} 
.calendar th { background: linear-gradient(135deg,#667eea,#764ba2); color: #fff; padding: 10px; font-weight: 700; text-align: center;} 
.calendar td { border: 2px solid #eef2f7; padding: 10px; text-align: center; width: 14%; height: 100px; vertical-align: top; }
.day-number { font-size: .85rem; color:#374151; display:block; margin-bottom: 6px; font-weight: 700;}
.trades { font-size: .72rem; color:#6b7280; display:block; margin-top: 6px; background: rgba(255,255,255,.75); padding: 2px 6px; border-radius: 10px; }
.badge { display:inline-block; padding: 2px 8px; border-radius: 10px; font-size: .75rem; border:1px solid #e5e7eb; background:#fff }
hr.sep { border: none; height: 1px; background: #eee; margin: 18px 0; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# -------------------------
# 🔐 Autenticación (email + password)
# -------------------------
if "auth" not in st.session_state:
    st.session_state.auth = {"user": None, "access_token": None}


def do_sign_in(email: str, password: str):
    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        st.session_state.auth["user"] = res.user
        st.session_state.auth["access_token"] = res.session.access_token if res.session else None
        return True, None
    except Exception as e:
        return False, str(e)
    # dentro de do_sign_in(...)
res = supabase.auth.sign_in_with_password({"email": email, "password": password})
supabase.postgrest.auth(res.session.access_token)   # <-- añade esto
st.session_state.auth["user"] = res.user
st.session_state.auth["access_token"] = res.session.access_token if res.session else None



def do_sign_up(email: str, password: str):
    try:
        res = supabase.auth.sign_up({"email": email, "password": password})
        # Dependiendo de tu configuración, puede requerir confirmación por email
        return True, None
    except Exception as e:
        return False, str(e)


def do_sign_out():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    supabase.postgrest.auth(None)  # <-- añade esto
    st.session_state.auth = {"user": None, "access_token": None}


# -------------------------
# 🧩 Utilidades de parsing/fechas
# -------------------------
TRADE_PATTERN = re.compile(r"^(?:(?P<sym>[A-Za-z0-9_]+):)?(?P<body>(?P<signed>[+-]?\d+)P|BE)$")

ES_DAYS = {
    0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves", 4: "Viernes", 5: "Sábado", 6: "Domingo"
}

MONTHS_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


def parse_trades_cell(cell: str):
    if pd.isna(cell):
        return []
    items = [s.strip() for s in str(cell).split("~") if str(s).strip()]
    parsed = []
    for it in items:
        m = TRADE_PATTERN.match(it)
        if not m:
            if it in {"None", "nan", "error", "-error", ""}: 
                continue
            parsed.append({"symbol": None, "points": 0, "is_be": False, "raw": it, "valid": False})
            continue
        sym = m.group("sym")
        body = m.group("body")
        if body == "BE":
            parsed.append({"symbol": sym, "points": 0, "is_be": True, "raw": it, "valid": True})
        else:
            pts = int(m.group("signed"))
            parsed.append({"symbol": sym, "points": pts, "is_be": False, "raw": it, "valid": True})
    return parsed


# -------------------------
# 🗄️ DAO — Acceso a datos (Supabase)
# -------------------------
@st.cache_data(show_spinner=False)
def fetch_trades(user_id: str) -> pd.DataFrame:
    """Obtiene la tabla normalizada 'trades' del usuario."""
    data = (
        supabase
        .table("trades")
        .select("id,user_id,fecha,semana,dia,symbol,points,is_be,raw,created_at")
        .eq("user_id", user_id)
        .order("fecha", desc=False)
        .execute()
    )
    rows = data.data if hasattr(data, "data") else []
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["id","user_id","fecha","semana","dia","symbol","points","is_be","raw","created_at"]) 
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    return df


def insert_trade_entries(user_id: str, fecha_val: date, semana_txt: str, dia_txt: str, trade_text: str):
    """Inserta en trades_raw y trades (normalizado)."""
    # 1) Guardar crudo
    supabase.table("trades_raw").insert({
        "user_id": user_id,
        "fecha": str(fecha_val),
        "semana": semana_txt,
        "dia": dia_txt,
        "trade": trade_text,
    }).execute()

    # 2) Parsear y normalizar
    parsed = parse_trades_cell(trade_text)
    batch = []
    for t in parsed:
        batch.append({
            "user_id": user_id,
            "fecha": str(fecha_val),
            "semana": semana_txt,
            "dia": dia_txt,
            "symbol": t["symbol"],
            "points": int(t["points"]),
            "is_be": bool(t["is_be"]),
            "raw": t.get("raw"),
        })
    if batch:
        supabase.table("trades").insert(batch).execute()


# -------------------------
# 📊 Cálculo de métricas
# -------------------------
@st.cache_data(show_spinner=False)
def compute_metrics(df_trades: pd.DataFrame):
    out = {}
    if df_trades.empty:
        return {
            "equity_df": pd.DataFrame(),
            "wins": 0, "loss": 0, "be_ct": 0,
            "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": np.nan, "expectancy": 0.0,
            "max_dd": 0,
        }

    df = df_trades.copy()
    df["pts"] = df.apply(lambda r: 0 if r.get("is_be", False) else int(r.get("points") or 0), axis=1)
    df = df.sort_values("fecha")

    # Equity global
    df_equity = df[["fecha","pts"]].copy()
    df_equity["equity"] = df_equity["pts"].cumsum()

    # Ganadores/Perdedores/BE (por fila normalizada)
    wins = (df["pts"] > 0).sum()
    loss = (df["pts"] < 0).sum()
    be_ct = (df.get("is_be", False) == True).sum()

    sum_wins = df.loc[df["pts"] > 0, "pts"].sum()
    sum_loss = -df.loc[df["pts"] < 0, "pts"].sum()

    tot = wins + loss
    win_rate = wins / tot if tot else 0.0
    avg_win = (sum_wins / wins) if wins else 0.0
    avg_loss = (sum_loss / loss) if loss else 0.0
    profit_factor = (sum_wins / sum_loss) if sum_loss else np.inf
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

    # Max Drawdown (global)
    roll_max = np.maximum.accumulate(df_equity["equity"].values)
    drawdowns = roll_max - df_equity["equity"].values
    max_dd = int(np.max(drawdowns)) if len(drawdowns) else 0

    return {
        "equity_df": df_equity,
        "wins": int(wins), "loss": int(loss), "be_ct": int(be_ct),
        "win_rate": float(win_rate), "avg_win": float(avg_win), "avg_loss": float(avg_loss),
        "profit_factor": float(profit_factor) if np.isfinite(profit_factor) else np.inf,
        "expectancy": float(expectancy),
        "max_dd": int(max_dd),
    }


# -------------------------
# 🧮 Helpers de vistas
# -------------------------

def month_filter(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    m = df[(pd.to_datetime(df["fecha"]).dt.year == year) & (pd.to_datetime(df["fecha"]).dt.month == month)].copy()
    return m


def calendar_html(year: int, month: int, daily_points: dict):
    def bg_color(puntos):
        if pd.isna(puntos):
            return "#ffffff"
        if puntos > 200: return "#d4edda"
        if puntos > 100: return "#e8f5e8"
        if puntos > 50:  return "#f0f9f0"
        if puntos > 0:   return "#f8fdf8"
        if puntos < -100: return "#f8d7da"
        if puntos < -50:  return "#fae6e8"
        if puntos < 0:    return "#fdf0f2"
        return "#ffffff"

    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdayscalendar(year, month)

    html = f"<table class='calendar'>\n<tr><th>DOM</th><th>LUN</th><th>MAR</th><th>MIE</th><th>JUE</th><th>VIE</th><th>SAB</th></tr>\n"
    for week in month_days:
        html += "<tr>"
        for d in week:
            if d == 0:
                html += "<td></td>"
            else:
                pts = daily_points.get(d, 0)
                color = bg_color(pts)
                html += f"<td style='background:{color}'>"
                html += f"<span class='day-number'>{d}</span>"
                html += f"<span class='badge'>{int(pts)}</span>"
                html += "</td>"
        html += "</tr>"
    html += "</table>"
    return html


# -------------------------
# 🔑 UI: Login / Registro
# -------------------------

def login_view():
    with st.container():
        st.markdown("""
        <div class="login-card">
          <div class="login-title">Trading Journal Pro</div>
          <div class="subtle">Inicia sesión o crea tu cuenta para continuar</div>
        </div>
        """, unsafe_allow_html=True)

    with st.form("auth_form", clear_on_submit=False):
        st.write("")
        col1, col2 = st.columns(2)
        with col1:
            mode = st.radio("Modo", ["Iniciar sesión", "Crear cuenta"], horizontal=True)
        with col2:
            pass
        email = st.text_input("Email", placeholder="tu@email.com")
        pw = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Continuar ✅")
        if submitted:
            if not email or not pw:
                st.error("Completa email y contraseña")
                return
            if mode == "Iniciar sesión":
                ok, err = do_sign_in(email, pw)
            else:
                ok, err = do_sign_up(email, pw)
                if ok:
                    st.success("Cuenta creada. Revisa tu email si se requiere confirmación. Ahora inicia sesión.")
                    return
            if not ok:
                st.error(f"Error: {err}")
            else:
                st.rerun()


# -------------------------
# 🧭 App principal (post-login)
# -------------------------

def app_view():
    user = st.session_state.auth.get("user")
    if not user:
        st.error("Sesión inválida. Vuelve a iniciar sesión.")
        do_sign_out(); st.stop()

    user_id = user.id

    st.sidebar.write(f"👤 Usuario: **{user.email}**")
    if st.sidebar.button("Cerrar sesión"):
        do_sign_out(); st.rerun()

    # Datos
    df = fetch_trades(user_id)

    # Filtros de periodo y símbolo
    years = sorted(pd.to_datetime(df["fecha"]).dt.year.unique()) if not df.empty else [datetime.now().year]
    year_sel = st.sidebar.selectbox("Año", years, index=len(years)-1)
    month_names = MONTHS_ES
    month_sel_name = st.sidebar.selectbox("Mes", month_names, index=(datetime.now().month-1))
    month_sel = month_names.index(month_sel_name) + 1

    symbols = sorted([s for s in df["symbol"].dropna().unique()]) if not df.empty else []
    sym_choice = st.sidebar.multiselect("Símbolos", options=symbols, default=symbols)

    st.title("📊 Trading Journal Pro — Beta")

    # Métricas globales
    metrics = compute_metrics(df)

    # Equity global
    col_g1, col_g2 = st.columns([2,1])
    with col_g1:
        st.subheader("Equity Global")
        if not metrics["equity_df"].empty:
            chart = alt.Chart(metrics["equity_df"]).mark_line(point=True).encode(
                x=alt.X("fecha:T", title="Fecha"),
                y=alt.Y("equity:Q", title="Puntos acumulados"),
                tooltip=["fecha:T", "equity:Q"],
            ).properties(height=360)
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Sin datos aún. Agrega tus primeros trades.")
    with col_g2:
        st.subheader("Resumen")
        c1, c2, c3 = st.columns(3)
        with c1: st.metric("Win Rate", f"{metrics['win_rate']*100:.1f}%")
        with c2: st.metric("Avg Win", f"{metrics['avg_win']:.1f}")
        with c3: st.metric("Avg Loss", f"-{metrics['avg_loss']:.1f}")
        c4, c5 = st.columns(2)
        with c4: st.metric("Profit Factor", f"{metrics['profit_factor']:.2f}" if np.isfinite(metrics['profit_factor']) else "∞")
        with c5: st.metric("Expectancy", f"{metrics['expectancy']:.2f}")
        st.caption(f"Max Drawdown: **{metrics['max_dd']}** pts")

    st.markdown("---")

    # Datos del mes
    df_m = month_filter(df, year_sel, month_sel)
    if sym_choice:
        df_m = df_m[(df_m["symbol"].isin(sym_choice)) | (df_m["symbol"].isna())]

    # Equity mensual
    st.subheader(f"Equity Mensual — {MONTHS_ES[month_sel-1]} {year_sel}")
    if not df_m.empty:
        df_m = df_m.copy()
        df_m["pts"] = df_m.apply(lambda r: 0 if r.get("is_be", False) else int(r.get("points") or 0), axis=1)
        df_m = df_m.sort_values("fecha")
        df_m_eq = df_m[["fecha","pts"]].copy()
        df_m_eq["equity_m"] = df_m_eq["pts"].cumsum()
        chart_m = alt.Chart(df_m_eq).mark_line(point=True).encode(
            x=alt.X("fecha:T", title="Fecha"),
            y=alt.Y("equity_m:Q", title="Puntos acumulados (mes)"),
            tooltip=["fecha:T", "equity_m:Q"],
        ).properties(height=300)
        st.altair_chart(chart_m, use_container_width=True)
    else:
        st.info("Sin trades en el mes seleccionado.")

    # Calendario mensual
    st.subheader("Calendario Mensual (suma por día)")
    if not df_m.empty:
        df_m_grp = df_m.groupby(pd.to_datetime(df_m["fecha"]).dt.day).agg(puntos=("points", lambda s: int(np.nansum([0 if np.isnan(x) else x for x in s])))).reset_index()
        daily_map = {int(r["fecha"] if "fecha" in r else r["day"]): int(r["puntos"]) for _, r in df_m_grp.rename(columns={"fecha":"day"}).iterrows()}
    else:
        daily_map = {}
    st.markdown(calendar_html(year_sel, month_sel, daily_map), unsafe_allow_html=True)

    st.markdown("---")

    # Tabs: Histórico | Agregar Trade | Meses
    tab1, tab2, tab3 = st.tabs(["📋 Histórico", "➕ Agregar Trade", "🗓️ Resumen por Meses"]) 

    with tab1:
        st.subheader("Histórico (filtrable)")
        if df.empty:
            st.info("No hay datos.")
        else:
            st.dataframe(df.sort_values("fecha", ascending=False), use_container_width=True)
            # Export CSV (normalizado)
            csv_bytes = df.to_csv(index=False).encode()
            st.download_button("Descargar CSV", csv_bytes, file_name="journal_normalizado.csv", mime="text/csv")

    with tab2:
        st.subheader("Agregar Nueva Entrada")
        with st.form("new_entry", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                f_fecha = st.date_input("Fecha", value=date.today())
            with c2:
                # Semana ISO
                semana_numero = datetime.combine(f_fecha, datetime.min.time()).isocalendar()[1]
                f_semana = st.text_input("Semana", value=f"Semana {semana_numero}")
            with c3:
                dia_txt = ES_DAYS[datetime.combine(f_fecha, datetime.min.time()).weekday()]
                f_dia = st.text_input("Día", value=dia_txt)
            f_trade = st.text_area("Trade(s)", placeholder="Ej: NQ:+50P ~ ES:-20P ~ NQ:BE", help="Separa con '~'. Usa 'BE' para Break Even.")
            submitted = st.form_submit_button("Guardar ✅")
            if submitted:
                if not f_trade.strip():
                    st.error("Ingresa al menos un trade.")
                else:
                    try:
                        insert_trade_entries(user_id, f_fecha, f_semana, f_dia, f_trade)
                        st.success("Guardado. Actualiza datos desde el menú si no ves cambios inmediatamente.")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Error guardando: {e}")

    with tab3:
        st.subheader("Resultados por Mes")
        if df.empty:
            st.info("No hay datos para resumir.")
        else:
            df_monthly = pd.DataFrame({
                "year": pd.to_datetime(df["fecha"]).dt.year,
                "month": pd.to_datetime(df["fecha"]).dt.month,
                "pts": df.apply(lambda r: 0 if r.get("is_be", False) else int(r.get("points") or 0), axis=1)
            })
            monthly = df_monthly.groupby(["year","month"]).agg(total_pts=("pts","sum"), trades=("pts","count")).reset_index()
            monthly["Periodo"] = monthly.apply(lambda r: f"{MONTHS_ES[int(r['month'])-1]} {int(r['year'])}", axis=1)
            monthly = monthly.sort_values(["year","month"]) 
            st.dataframe(monthly[["Periodo","total_pts","trades"]].rename(columns={"total_pts":"Puntos","trades":"#Trades"}), use_container_width=True)


# -------------------------
# 🚀 Router
# -------------------------
if st.session_state.auth.get("user") is None:
    login_view()
else:
    app_view()

# -------------------------
# 📎 Notas de configuración (para que no se pierdan)
# -------------------------
with st.expander("ℹ️ Instrucciones de Supabase (admin)"):
    st.markdown(
        """
        **Tablas sugeridas**

        1) `trades_raw`
        ```sql
        create table if not exists trades_raw (
          id bigserial primary key,
          user_id uuid not null,
          fecha date not null,
          semana text,
          dia text,
          trade text not null,
          created_at timestamptz default now()
        );
        ```

        2) `trades`
        ```sql
        create table if not exists trades (
          id bigserial primary key,
          user_id uuid not null,
          fecha date not null,
          semana text,
          dia text,
          symbol text,
          points int,
          is_be boolean default false,
          raw text,
          created_at timestamptz default now()
        );
        ```

        **RLS Policies** (activar Row Level Security y políticas por usuario)
        ```sql
        alter table trades_raw enable row level security;
        alter table trades enable row level security;

        create policy "trades_raw select own" on trades_raw for select using (auth.uid() = user_id);
        create policy "trades_raw insert own" on trades_raw for insert with check (auth.uid() = user_id);

        create policy "trades select own" on trades for select using (auth.uid() = user_id);
        create policy "trades insert own" on trades for insert with check (auth.uid() = user_id);
        ```

        **Secrets** (Streamlit Cloud → App Settings → Secrets)
        ```
        SUPABASE_URL = https://<tu-proyecto>.supabase.co
        SUPABASE_ANON_KEY = <clave_anon>
        ```
        """
    )


