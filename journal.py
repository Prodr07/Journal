import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
from datetime import datetime, date
import calendar
import re

st.set_page_config(page_title="Trading Journal Pro — Supabase", layout="wide")

from supabase import create_client



# =========================
# ⚙️ Conexión Supabase
# =========================

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_ANON_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Faltan SUPABASE_URL y/o SUPABASE_ANON_KEY en st.secrets")
    st.stop()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =========================
# 🎨 Estilos mínimos
# =========================
st.markdown(
    """
    <style>
    .login-card{max-width:420px;margin:6vh auto;padding:24px;border-radius:16px;box-shadow:0 8px 24px rgba(0,0,0,.08);background:#fff}
    .login-title{font-weight:800;font-size:1.5rem;margin-bottom:.5rem;background:linear-gradient(135deg,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
    .subtle{color:#6b7280;font-size:.92rem;margin-bottom:.75rem}
    .badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.75rem;border:1px solid #e5e7eb;background:#fff}
    .calendar{width:100%;border-collapse:collapse;margin-bottom:20px;box-shadow:0 2px 5px rgba(0,0,0,.06)}
    .calendar th{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:10px;font-weight:700;text-align:center}
    .calendar td{border:2px solid #eef2f7;padding:10px;text-align:center;width:14%;height:100px;vertical-align:top}
    .day-number{font-size:.85rem;color:#374151;display:block;margin-bottom:6px;font-weight:700}
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================
# 🔐 Sesión / Auth helpers
# =========================
if "auth" not in st.session_state:
    st.session_state.auth = {"user": None, "access_token": None}


def do_sign_in(email: str, password: str):
    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        # Aplicar token al cliente PostgREST (necesario para RLS)
        if res.user and res.session:
    st.session_state.auth = {
        "user": res.user,
        "access_token": res.session.access_token
    }
    # Guarda el token localmente (persistente entre refresh)
    st.experimental_set_query_params(token=res.session.access_token)
        return True, None
    except Exception as e:
        return False, str(e)


def do_sign_up(email: str, password: str):
    try:
        res = supabase.auth.sign_up({"email": email, "password": password})
        # Si tu proyecto no requiere confirmación, puede devolver sesión inmediata
        if getattr(res, "session", None) and res.session.access_token:
            supabase.postgrest.auth(res.session.access_token)
        return True, None
    except Exception as e:
        return False, str(e)


def do_sign_out():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass
    st.session_state.auth = {"user": None, "access_token": None}
    st.experimental_set_query_params()  # Limpia el token
    st.rerun()


# =========================
# 🧩 Parsing de trades
# Soporta: "NQ:+50P", "ES:-20P", "BE", "+30P", "-10P" y múltiples con '~'
# =========================
TRADE_PATTERN = re.compile(r"^(?:(?P<sym>[A-Za-z0-9_]+):)?(?P<body>(?P<signed>[+-]?\d+)P|BE)$")
ES_DAYS = {0:"Lunes",1:"Martes",2:"Miércoles",3:"Jueves",4:"Viernes",5:"Sábado",6:"Domingo"}
MONTHS_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


def parse_trades_cell(cell: str):
    if cell is None:
        return []
    items = [s.strip() for s in str(cell).split("~") if str(s).strip()]
    parsed = []
    for it in items:
        m = TRADE_PATTERN.match(it)
        if not m:
            if it in {"None","nan","error","-error",""}:
                continue
            parsed.append({"symbol": None, "points": 0, "is_be": False, "raw": it})
            continue
        sym = m.group("sym")
        body = m.group("body")
        if body == "BE":
            parsed.append({"symbol": sym, "points": 0, "is_be": True, "raw": it})
        else:
            pts = int(m.group("signed"))
            parsed.append({"symbol": sym, "points": pts, "is_be": False, "raw": it})
    return parsed

# =========================
# 🗄️ DAO — Acceso a datos (solo public."Trades")
# =========================
@st.cache_data(show_spinner=False)
def fetch_trades(user_id: str) -> pd.DataFrame:
    data = (
        supabase
        .table("Trades")  # comillas porque la tabla tiene T mayúscula
        .select('id,"User_id",fecha,semana,dia,symbol,point,be,trade,created_at')
        .eq("User_id", user_id)
        .order("fecha", desc=False)
        .execute()
    )
    rows = data.data if hasattr(data, "data") else []
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["id","User_id","fecha","semana","dia","symbol","point","be","trade","created_at"]) 
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    return df


def insert_trade_entries(user_id: str, fecha_val: date, semana_txt: str, dia_txt: str, trade_text: str):
    parsed = parse_trades_cell(trade_text)
    batch = []
    for t in parsed:
        batch.append({
            "User_id": user_id,
            "fecha": str(fecha_val),
            "semana": semana_txt,
            "dia": dia_txt,
            "symbol": t["symbol"],
            "point": int(t["points"]),
            "be": bool(t["is_be"]),
            "trade": trade_text,
        })
    if batch:
        supabase.table("Trades").insert(batch).execute()


# =========================
# 🧮 Helpers de vistas
# =========================
MONTHS = MONTHS_ES

def month_filter(df: pd.DataFrame, year: int, month: int) -> pd.DataFrame:
    return df[(pd.to_datetime(df["fecha"]).dt.year == year) & (pd.to_datetime(df["fecha"]).dt.month == month)].copy()


def calendar_html(year: int, month: int, daily_points: dict):
    def bg_color(p):
        if pd.isna(p):
            return "#ffffff"
        if p > 200: return "#d4edda"
        if p > 100: return "#e8f5e8"
        if p > 50:  return "#f0f9f0"
        if p > 0:   return "#f8fdf8"
        if p < -100: return "#f8d7da"
        if p < -50:  return "#fae6e8"
        if p < 0:    return "#fdf0f2"
        return "#ffffff"

    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdayscalendar(year, month)
    html = "<table class='calendar'>\n<tr><th>DOM</th><th>LUN</th><th>MAR</th><th>MIE</th><th>JUE</th><th>VIE</th><th>SAB</th></tr>\n"
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


# =========================
# 📊 Métricas
# =========================
@st.cache_data(show_spinner=False)
def compute_metrics(df_trades: pd.DataFrame):
    if df_trades.empty:
        return {
            "equity_df": pd.DataFrame(),
            "wins": 0, "loss": 0, "be_ct": 0,
            "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": np.nan, "expectancy": 0.0,
            "max_dd": 0,
        }

    df = df_trades.copy()
    df["point"] = df["point"].astype(float)
    df["pts"] = df.apply(lambda r: 0 if bool(r.get("be", False)) else int(r.get("point") or 0), axis=1)
    df = df.sort_values("fecha")

    # Equity global
    df_equity = df[["fecha","pts"]].copy()
    df_equity["equity"] = df_equity["pts"].cumsum()

    wins = (df["pts"] > 0).sum()
    loss = (df["pts"] < 0).sum()
    be_ct = (df.get("be", False) == True).sum()

    sum_wins = df.loc[df["pts"] > 0, "pts"].sum()
    sum_loss = -df.loc[df["pts"] < 0, "pts"].sum()

    tot = wins + loss
    win_rate = wins / tot if tot else 0.0
    avg_win = (sum_wins / wins) if wins else 0.0
    avg_loss = (sum_loss / loss) if loss else 0.0
    profit_factor = (sum_wins / sum_loss) if sum_loss else np.inf
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

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
    # Revisar si hay token persistente en la URL
params = st.experimental_get_query_params()
if "token" in params:
    token = params["token"][0]
    supabase.postgrest.auth(token)
    user = supabase.auth.get_user(token)
    if user and user.user:
        st.session_state.auth = {"user": user.user, "access_token": token}

# =========================
# 🔑 Login UI
# =========================

def login_view():
    st.markdown('<div class="login-card"><div class="login-title">Trading Journal Pro</div><div class="subtle">Inicia sesión o crea tu cuenta para continuar</div></div>', unsafe_allow_html=True)
    with st.form("auth_form", clear_on_submit=False):
        mode = st.radio("Modo", ["Iniciar sesión", "Crear cuenta"], horizontal=True)
        email = st.text_input("Email")
        pw = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Continuar ✅")
        if submitted:
            if not email or not pw:
                st.error("Completa email y contraseña")
                return
            if mode == "Iniciar sesión":
                ok, err = do_sign_in(email, pw)
                if not ok:
                    st.error(f"Error: {err}")
                else:
                    st.rerun()
            else:
                ok, err = do_sign_up(email, pw)
                if ok:
                    st.success("Cuenta creada. Si tu proyecto requiere confirmación por email, revísalo. Luego inicia sesión.")
                else:
                    st.error(f"Error: {err}")

# =========================
# 🧭 App principal
# =========================

def app_view():
    user = st.session_state.auth.get("user")
    if not user:
        st.error("Sesión inválida. Vuelve a iniciar sesión.")
        do_sign_out(); st.stop()

    # Asegura token aplicado si se recarga la página
    if st.session_state.auth.get("access_token"):
        supabase.postgrest.auth(st.session_state.auth["access_token"])

    st.sidebar.write(f"👤 Usuario: **{user.email}**")
    if st.sidebar.button("Cerrar sesión"):
        do_sign_out(); st.rerun()

    # Datos
    df = fetch_trades(user.id)

    # Filtros
    years = sorted(pd.to_datetime(df["fecha"]).dt.year.unique()) if not df.empty else [datetime.now().year]
    year_sel = st.sidebar.selectbox("Año", years, index=len(years)-1)
    month_name = st.sidebar.selectbox("Mes", MONTHS, index=(datetime.now().month-1))
    month_sel = MONTHS.index(month_name) + 1

    symbols = sorted([s for s in df["symbol"].dropna().unique()]) if not df.empty else []
    sym_choice = st.sidebar.multiselect("Símbolos", options=symbols, default=symbols)

    st.title("📊 Trading Journal Pro — Supabase")

   # Mes actual filtrado
    df_m = month_filter(df, year_sel, month_sel)
    if sym_choice:
        df_m = df_m[(df_m["symbol"].isin(sym_choice)) | (df_m["symbol"].isna())]

    st.subheader(f"Equity Mensual — {MONTHS[month_sel-1]} {year_sel}")
    if not df_m.empty:
        df_m = df_m.copy()
        df_m["point"] = df_m["point"].astype(float)
        df_m["pts"] = df_m.apply(lambda r: 0 if bool(r.get("be", False)) else int(r.get("point") or 0), axis=1)
        df_m = df_m.sort_values("fecha")
        df_m_eq = df_m[["fecha","pts"]].copy()
        df_m_eq["equity_m"] = df_m_eq["pts"].cumsum()
        chart_m = alt.Chart(df_m_eq).mark_line(point=True).encode(
            x=alt.X("fecha:T", title="Fecha"),
            y=alt.Y("equity_m:Q", title="Puntos acumulados (mes)"),
            tooltip=["fecha:T","equity_m:Q"],
        ).properties(height=300)
        st.altair_chart(chart_m, use_container_width=True)
    else:
        st.info("Sin trades en el mes seleccionado.")


          # Calendario mensual (suma por día)
    st.subheader("Calendario Mensual")
    if not df_m.empty:
        df_m_grp = df_m.groupby(pd.to_datetime(df_m["fecha"]).dt.day).agg(puntos=("point", lambda s: int(np.nansum([0 if pd.isna(x) else x for x in s])))).reset_index()
        daily_map = {int(r["fecha"] if "fecha" in r else r["day"]): int(r["puntos"]) for _, r in df_m_grp.rename(columns={"fecha":"day"}).iterrows()}
    else:
        daily_map = {}
    st.markdown(calendar_html(year_sel, month_sel, daily_map), unsafe_allow_html=True)

    st.markdown("---")


    # Métricas globales
    metrics = compute_metrics(df)
    colA, colB = st.columns([2,1])
    with colA:
        st.subheader("Equity Global")
        if not metrics["equity_df"].empty:
            chart = alt.Chart(metrics["equity_df"]).mark_line(point=True).encode(
                x=alt.X("fecha:T", title="Fecha"),
                y=alt.Y("equity:Q", title="Puntos acumulados"),
                tooltip=["fecha:T","equity:Q"],
            ).properties(height=360)
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Sin datos aún. Agrega tus primeros trades.")
    with colB:
        st.subheader("Resumen")
        c1,c2,c3 = st.columns(3)
        with c1: st.metric("Win Rate", f"{metrics['win_rate']*100:.1f}%")
        with c2: st.metric("Avg Win", f"{metrics['avg_win']:.1f}")
        with c3: st.metric("Avg Loss", f"-{metrics['avg_loss']:.1f}")
        c4,c5 = st.columns(2)
        with c4: st.metric("Profit Factor", f"{metrics['profit_factor']:.2f}" if np.isfinite(metrics['profit_factor']) else "∞")
        with c5: st.metric("Expectancy", f"{metrics['expectancy']:.2f}")
        st.caption(f"Max Drawdown: **{metrics['max_dd']}** pts")

    st.markdown("---")

 
  

    # Tabs
    tab1, tab2, tab3 = st.tabs(["📋 Histórico", "➕ Agregar Trade", "🗓️ Resumen por Meses"]) 

    with tab1:
        st.subheader("Histórico (filtrable)")
        if df.empty:
            st.info("No hay datos.")
        else:
            st.dataframe(df.sort_values("fecha", ascending=False), use_container_width=True)
            csv_bytes = df.to_csv(index=False).encode()
            st.download_button("Descargar CSV", csv_bytes, file_name="journal.csv", mime="text/csv")

    with tab2:
        st.subheader("Agregar Nueva Entrada")
        with st.form("new_entry", clear_on_submit=True):
            c1,c2,c3 = st.columns(3)
            with c1:
                f_fecha = st.date_input("Fecha", value=date.today())
            with c2:
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
                        insert_trade_entries(user.id, f_fecha, f_semana, f_dia, f_trade)
                        st.success("Guardado. Si no ves cambios, usa Rerun en el menú.")
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
                "pts": df.apply(lambda r: 0 if bool(r.get("be", False)) else int(r.get("point") or 0), axis=1)
            })
            monthly = df_monthly.groupby(["year","month"]).agg(total_pts=("pts","sum"), trades=("pts","count")).reset_index()
            monthly["Periodo"] = monthly.apply(lambda r: f"{MONTHS[int(r['month'])-1]} {int(r['year'])}", axis=1)
            monthly = monthly.sort_values(["year","month"]) 
            st.dataframe(monthly[["Periodo","total_pts","trades"]].rename(columns={"total_pts":"Puntos","trades":"#Trades"}), use_container_width=True)

# =========================
# 🚦 Router
# =========================
if st.session_state.auth.get("user") is None:
    login_view()
else:
    app_view()













