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

def _safe_pts(row, colname="point"):
    """Devuelve puntos/porcentaje como float.
    Soporta None, '', 'nan', '2.5', '2.5%', etc. y respeta BE."""
    if bool(row.get("be", False)):
        return 0.0
    v = row.get(colname, 0)
    if v is None:
        return 0.0
    if isinstance(v, str):
        v = v.strip()
        if v.endswith("%"):
            v = v[:-1]  # quita el símbolo
        if v == "" or v.lower() in {"nan", "none"}:
            return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def do_sign_in(email: str, password: str):
    try:
        res = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if res.user and res.session:
            token = res.session.access_token
            # Aplica el token al cliente PostgREST (para que las queries tengan RLS)
            supabase.postgrest.auth(token)
            # Guarda la sesión en Streamlit
            st.session_state.auth = {"user": res.user, "access_token": token}
            # Guarda el token localmente en la URL (persiste tras refresh)
            st.experimental_set_query_params(token=token)
            return True, None
        else:
            return False, "Credenciales inválidas"
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
    st.experimental_set_query_params()  # Limpia token en URL
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
            parsed.append({"symbol": None, "porcentaje": 0, "is_be": False, "raw": it})
            continue
        sym = m.group("sym")
        body = m.group("body")
        if body == "BE":
            parsed.append({"symbol": sym, "porcentaje": 0, "is_be": True, "raw": it})
        else:
            pts = int(m.group("signed"))
            parsed.append({"symbol": sym, "porcentaje": pts, "is_be": False, "raw": it})
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
            "point": int(t["porcentaje"]),
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


import calendar as _pycal

def calendar_html(year: int, month: int, daily_points: dict[int, float], daily_counts: dict[int, int] | None = None) -> str:
    """
    daily_points: {dia -> suma_de_puntos_o_%}
    daily_counts: {dia -> cantidad_de_trades} (opcional)
    """

    # ===== helpers de color (verde para +, rojo para -) =====
    vals = list(daily_points.values()) if daily_points else [0]
    max_abs = max(1, max(abs(v) for v in vals))

    def bg_for(v: float) -> str:
        """Color de fondo según magnitud relativa."""
        if v is None:
            return "#101317"  # vacío
        if v == 0:
            return "#151A21"  # neutro
        # intensidad 0..1
        t = min(1.0, abs(v) / max_abs)
        # color base
        if v > 0:
            # verdes
            # mezclar #1C2B23 (oscuro) con #1F4630 (más brillante)
            r1,g1,b1 = (0x1C, 0x2B, 0x23)
            r2,g2,b2 = (0x1F, 0x46, 0x30)
        else:
            # rojos
            r1,g1,b1 = (0x2B, 0x1C, 0x21)
            r2,g2,b2 = (0x46, 0x1F, 0x2C)
        r = int(r1 + (r2-r1)*t)
        g = int(g1 + (g2-g1)*t)
        b = int(b1 + (b2-b1)*t)
        return f"rgb({r},{g},{b})"

    def txt_for(v: float) -> str:
        if v is None or v == 0:
            return "#cbd5e1"  # gris claro
        return "#6ee7b7" if v > 0 else "#fca5a5"  # verde/rojo claro

    # ===== CSS =====
    css = """
    <style>
    .cal-wrap{width:100%;overflow-x:auto}
    table.cal{width:100%;border-collapse:separate;border-spacing:10px;}
    .cal thead th{
      background: linear-gradient(135deg,#5662D6,#6C49B8);
      color:#fff;text-align:center;padding:14px;border-radius:12px;
      font-weight:800;letter-spacing:.03em
    }
    .cal td{
      background:#101317;border-radius:14px;vertical-align:top;
      height:120px;padding:10px 10px; position:relative;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,.04);
    }
    .cal .day-badge{
      position:absolute;top:8px;left:10px;
      width:28px;height:28px;border-radius:50%;
      display:flex;align-items:center;justify-content:center;
      font-weight:700;background:#0f172a;color:#e2e8f0;border:1px solid rgba(255,255,255,.05)
    }
    .cal .value{
      margin-top:34px;text-align:center;font-weight:800;font-size:22px;
      line-height:1;color:#e2e8f0;text-shadow:0 1px 0 rgba(0,0,0,.25)
    }
    .cal .pill{
      margin:8px auto 0 auto;display:inline-block;min-width:84px;text-align:center;
      padding:6px 10px;border-radius:999px;font-size:12px;
      background:rgba(255,255,255,.06);color:#cbd5e1;border:1px solid rgba(255,255,255,.07)
    }
    .cal .muted{opacity:.35}
    </style>
    """

    # ===== Cabecera y grilla =====
    cal = _pycal.Calendar(firstweekday=6)  # Domingo
    weeks = cal.monthdayscalendar(year, month)
    header = "<tr>" + "".join(f"<th>{d}</th>" for d in ["DOM","LUN","MAR","MIE","JUE","VIE","SAB"]) + "</tr>"

    body_rows = []
    for week in weeks:
        tds = []
        for d in week:
            if d == 0:
                tds.append('<td class="muted"></td>')
                continue

            val = float(daily_points.get(d, 0)) if d in daily_points else 0.0
            cnt = daily_counts.get(d, 0) if (daily_counts is not None) else None
            bg = bg_for(val)
            c  = txt_for(val)

            # Formato del valor grande (usa % si lo tuyo ahora es porcentaje)
            val_txt = f"{int(val)}"  # o f"{val:.1f}%" si quieres 1 decimal

            pill_txt = f"{cnt} trade" + ("s" if (cnt or 0) != 1 else "") if cnt is not None else ""

            tds.append(
                f"""
                <td style="background:{bg}">
                  <div class="day-badge">{d}</div>
                  <div class="value" style="color:{c}">{val_txt}</div>
                  {f'<div class="pill">{pill_txt}</div>' if cnt is not None else ''}
                </td>
                """
            )
        body_rows.append("<tr>" + "".join(tds) + "</tr>")

    html = f"""
    <div class="cal-wrap">
      {css}
      <table class="cal">
        <thead>{header}</thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
    </div>
    """
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
# Si tu columna se llama "point"
    df["pts"] = df.apply(lambda r: _safe_pts(r, "point"), axis=1)

# Si la cambiaste a "porcentaje", usa esta en su lugar:
# df["pts"] = df.apply(lambda r: _safe_pts(r, "porcentaje"), axis=1)

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
        st.error("Sesión inválida."); do_sign_out(); st.stop()

    # Asegura token para RLS
    if st.session_state.auth.get("access_token"):
        supabase.postgrest.auth(st.session_state.auth["access_token"])

    st.sidebar.write(f"👤 Usuario: **{user.email}**")
    if st.sidebar.button("Cerrar sesión"):
        do_sign_out(); st.rerun()

    # 1) Cargar datos
    df = fetch_trades(user.id)

    # 2) Filtros
    years = sorted(pd.to_datetime(df["fecha"]).dt.year.unique()) if not df.empty else [datetime.now().year]
    year_sel = st.sidebar.selectbox("Año", years, index=len(years)-1)
    month_name = st.sidebar.selectbox("Mes", MONTHS, index=(datetime.now().month-1))
    month_sel = MONTHS.index(month_name) + 1
    symbols = sorted([s for s in df["symbol"].dropna().unique()]) if not df.empty else []
    sym_choice = st.sidebar.multiselect("Símbolos", options=symbols, default=symbols)

    st.title("📊 Trading Journal Pro — Supabase")

    # 3) Mes actual filtrado
    df_m = month_filter(df, year_sel, month_sel)
    if sym_choice:
        df_m = df_m[(df_m["symbol"].isin(sym_choice)) | (df_m["symbol"].isna())]

    # Calcula pts una vez
    if not df_m.empty:
        df_m = df_m.copy()
        df_m["pts"] = df_m.apply(lambda r: _safe_pts(r, "point"), axis=1)  # o "porcentaje" si renombraste
        df_m = df_m.sort_values("fecha")

    # === Calendario primero ===
    st.subheader(f"Calendario Mensual — {MONTHS[month_sel-1]} {year_sel}")
    if not df_m.empty:
        df_m["day"] = pd.to_datetime(df_m["fecha"]).dt.day
        daily_map = df_m.groupby("day")["pts"].sum().to_dict()
    else:
        daily_map = {}
    st.markdown(calendar_html(year_sel, month_sel, daily_map), unsafe_allow_html=True)
# df_m: DataFrame del mes filtrado (ya con columnas: fecha, point, be, etc.)
# Si ahora trabajas en %, usa la columna 'point' como porcentaje.
df_m_cal = df_m.copy()
df_m_cal["day"] = pd.to_datetime(df_m_cal["fecha"]).dt.day

# Suma del día (ignora BE si be == True)
df_m_cal["pts"] = df_m_cal.apply(lambda r: 0 if bool(r.get("be", False)) else float(r.get("point") or 0), axis=1)

daily_points = df_m_cal.groupby("day")["pts"].sum().to_dict()          # {día: suma}
daily_counts = df_m_cal.groupby("day")["pts"].count().to_dict()        # {día: cantidad}

st.subheader(f"Calendario Mensual — {MONTHS[month_sel-1]} {year_sel}")
html = calendar_html(year_sel, month_sel, daily_points, daily_counts)
st.markdown(html, unsafe_allow_html=True)

    st.markdown("---")

    # === Equity mensual después ===
    st.subheader(f"Equity Mensual — {MONTHS[month_sel-1]} {year_sel}")
    if not df_m.empty:
        df_m_eq = df_m[["fecha","pts"]].copy()
        df_m_eq["equity_m"] = df_m_eq["pts"].cumsum()
        chart_m = alt.Chart(df_m_eq).mark_line(point=True).encode(
            x=alt.X("fecha:T", title="Fecha"),
            y=alt.Y("equity_m:Q", title="Acumulado (mes)"),
            tooltip=["fecha:T","equity_m:Q"],
        ).properties(height=300)
        st.altair_chart(chart_m, use_container_width=True)
    else:
        st.info("Sin trades en el mes seleccionado.")




    # Métricas globales
    metrics = compute_metrics(df)
    colA, colB = st.columns([2,1])
    with colA:
        st.subheader("Equity Global")
        if not metrics["equity_df"].empty:
            chart = alt.Chart(metrics["equity_df"]).mark_line(point=True).encode(
                x=alt.X("fecha:T", title="Fecha"),
                y=alt.Y("equity:Q", title="porcentaje acumulados"),
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
                "pts": df.apply(lambda r: _safe_pts(r, "point"), axis=1)
# o:
# "pts": df.apply(lambda r: _safe_pts(r, "porcentaje"), axis=1)

            })
            monthly = df_monthly.groupby(["year","month"]).agg(total_pts=("pts","sum"), trades=("pts","count")).reset_index()
            monthly["Periodo"] = monthly.apply(lambda r: f"{MONTHS[int(r['month'])-1]} {int(r['year'])}", axis=1)
            monthly = monthly.sort_values(["year","month"]) 
            st.dataframe(monthly[["Periodo","total_pts","trades"]].rename(columns={"total_pts":"porcentaje","trades":"#Trades"}), use_container_width=True)

# =========================
# 🚦 Router
# =========================
if st.session_state.auth.get("user") is None:
    login_view()
else:
    app_view()



























